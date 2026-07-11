import { nextTick, reactive, ref, shallowRef, type Ref } from 'vue'
import type { RouteLocationNormalizedLoaded } from 'vue-router'
import { ElMessage, ElLoading } from 'element-plus'
import SearchResultList from '@/components/SearchResultList.vue'
import { listConfs, searchPapers, type PaperItem } from '@/api/paper'
import { suggestKeywordsWithSettings } from '@/api/ai'
import { loadAiSettings, loadApiKey, toApiPayload } from '@/utils/aiSettings'
import { useI18n } from '@/utils/i18n'
// prettier-ignore
import { parseDsl, splitForBackend, normalizeQueryInput, type AstNode } from '@/utils/queryDsl'
const MAX_FETCH = 5000
const PAGE_SIZE = 200
type SearchMeta = { total: number; fetched: number; truncated: boolean }
type SearchResultRef = InstanceType<typeof SearchResultList> | null
export function useHomeSearch() {
  const { t } = useI18n()
  const firstEntry = ref(true)
  const availableConfs = shallowRef<string[]>([])
  // Search state is intentionally minimal now that the WoS-style DSL drives
  // every refinement (field tags, year range, venue list, etc.) from the
  // single ``query`` string. The ``sp_*`` fields are kept only so the
  // existing handlers (e.g. ``handleSearchAuthor``) can populate the DSL
  // without reshaping their public API.
  const searchContent = reactive({
    query: '',
    sp_author: '',
    confs: [] as string[]
  })
  const queryResult = shallowRef<Record<string, Record<string, PaperItem[]>>>(
    {}
  )
  const searchMeta = ref<SearchMeta>({ total: 0, fetched: 0, truncated: false })
  const activeAst = shallowRef<AstNode>({ kind: 'empty' })
  const guessLoading = ref(false)
  const guessList = ref<string[]>([])
  const guessProviderLabel = ref('')
  // Pinned at the moment the user issues a search so the right-sidebar AI
  // "guess" panel can be re-fed the *original* topic on every subsequent
  // search, instead of a query that already contains an OR-merged keyword
  // list from a previous AI dialog pick. Without this, the OR-merged string
  // is sent straight back into the LLM as a topic prompt, which destroys
  // keyword quality ("time series agent" + the merged words are themselves
  // the prompt the LLM sees).
  const originalTopic = ref('')

  let externalRef: Ref<SearchResultRef> | null = null
  const setSearchResultRef = (r: Ref<SearchResultRef>): void => {
    externalRef = r
  }

  const groupByConfYear = (items: PaperItem[]) => {
    const out: Record<string, Record<string, PaperItem[]>> = {}
    for (const it of items) {
      const conf = it.conf || 'UNKNOWN'
      const year = String(it.year || 'NA')
      if (!out[conf]) out[conf] = {}
      if (!out[conf][year]) out[conf][year] = []
      out[conf][year].push(it)
    }
    return out
  }

  const buildBaseQuery = () => {
    // ``field`` is intentionally omitted: the backend defaults to a
    // multi-field topic search (title + author + abstract). The DSL splitter
    // hoists any explicit author / venue / year qualifiers from the user's
    // expression below, and the residual AST is re-applied client-side.
    const params: Record<string, unknown> = { sort: '-year' }
    // Normalise CJK fullwidth punctuation only at submission time so the input
    // box itself stays untouched as the user types. This is the single choke
    // point through which every search request flows.
    const rawQuery = normalizeQueryInput(searchContent.query || '')
    const ast = parseDsl(rawQuery)
    const split = splitForBackend(ast, 'any')
    activeAst.value = split.residual
    if (split.q) {
      params.q = split.q
    } else if (rawQuery && ast.kind === 'empty') {
      // Fallback: parser produced no AST at all (e.g. malformed input). Forward
      // the raw text so the backend still narrows things before we filter
      // locally. We intentionally do *not* fall back when the AST parsed cleanly
      // into pure field-qualified clauses (e.g. ``AU="Xiaowen Jiang"``), because
      // re-sending the DSL syntax as a free-text ``q`` would AND a guaranteed-
      // zero substring filter on top of the (already correct) ``author`` param
      // and silently wipe out every hit.
      params.q = rawQuery
    } else if (
      rawQuery &&
      !split.author &&
      !split.conf &&
      split.since == null &&
      split.until == null
    ) {
      // Pure free-text query that the splitter couldn't hoist into a coarse
      // ``q`` (top-level OR, NOT, NEAR, or field-qualified topic terms that
      // stayed in residual). The residual AST still drives the precise
      // client-side re-evaluation, but the backend needs a coarse AND pre-
      // filter to keep from returning the whole corpus (≈621k papers). Without
      // this, the "All venues" badge, match-total counter, and truncated-
      // warning all key off garbage numbers from the full-corpus fetch.
      //
      // Prefer ``originalTopic`` (the user's pre-OR-merge seed), cleaning any
      // residual syntax so a dirty URL-loaded topic still narrows something.
      // Falls back to the cleaned raw query when ``originalTopic`` is empty
      // (e.g. a brand-new direct text search that happens to use OR).
      const clean = (s: string): string =>
        s
          .replace(/[()"]/g, ' ')
          .replace(/\s+OR\s+/gi, ' ')
          .replace(/\s+/g, ' ')
          .trim()
      const seed = clean(originalTopic.value) || clean(rawQuery)
      if (seed) params.q = seed
    }
    const author = split.author ?? searchContent.sp_author
    if (author) params.author = author
    if (split.since != null && split.until != null) {
      params.since = split.since
      params.until = split.until
    } else if (split.since != null) {
      params.since = split.since
    }
    if (split.conf && split.conf.length > 0) {
      params.conf = split.conf
    } else if (
      searchContent.confs.length > 0 &&
      searchContent.confs.length < availableConfs.value.length
    ) {
      params.conf = [...searchContent.confs]
    }
    return params
  }

  const handleTreeClick = (data: {
    level: number
    key?: string
    parent?: string
  }): void => {
    const r = externalRef?.value
    if (r) (r as any).filterResult(queryResult.value, data)
  }

  const search = (): void => {
    if (searchContent.query === '' && searchContent.sp_author === '') {
      ElMessage.warning(t('search.warn.empty'))
      return
    }
    // Heuristic to keep ``originalTopic`` in sync with whatever the user
    // actually searched for. A pure topic (no `` OR `` operator) is by
    // definition a fresh query — overwrite. An OR-merged query keeps
    // whatever originalTopic was pinned by ``handleAiSearchPick`` /
    // ``handleSearchGuess`` / ``consumeQueryParam`` so the right sidebar
    // keeps re-grounding on the original seed rather than the merged
    // composite.
    if (!/\s+OR\s+/i.test(searchContent.query))
      originalTopic.value = searchContent.query.trim()
    const loading = ElLoading.service({
      lock: true,
      text: t('search.button') + '...'
    })
    queryResult.value = {}
    searchMeta.value = { total: 0, fetched: 0, truncated: false }
    guessList.value = []
    const baseParams = buildBaseQuery()
    // Phase 1: probe total count with a minimal payload, then phase 2 fetches
    // up to MAX_FETCH (paginated by PAGE_SIZE because the backend caps each
    // page at settings.max_page_size = 200). The sidebar buckets / facets /
    // sort / pagination then operate on the *full* result set instead of an
    // arbitrary first page.
    searchPapers({ ...baseParams, page: 1, size: 1 })
      .then(async probe => {
        const total = probe.meta?.total ?? 0
        if (total === 0) {
          queryResult.value = {}
          searchMeta.value = { total: 0, fetched: 0, truncated: false }
          // Switch from the hero view to the results view *before* invoking
          // handleTreeClick, otherwise the <SearchResultList> child is not
          // mounted yet (firstEntry === true) and ``searchResult.value`` is
          // null, so the very first search would silently drop its payload
          // and the user would only see results after clicking Search a
          // second time. ``nextTick`` waits for the v-else branch to mount.
          firstEntry.value = false
          await nextTick()
          handleTreeClick({ level: 1 })
          return
        }
        const target = Math.min(total, MAX_FETCH)
        const truncated = total > MAX_FETCH
        const pages = Math.ceil(target / PAGE_SIZE)
        const collected: PaperItem[] = []
        for (let page = 1; page <= pages; page += 1) {
          const remaining = target - collected.length
          const pageSize = Math.min(PAGE_SIZE, remaining)
          const resp = await searchPapers({
            ...baseParams,
            page,
            size: pageSize
          })
          const items = resp.items || []
          collected.push(...items)
          if (items.length < pageSize) break
        }
        queryResult.value = groupByConfYear(collected)
        searchMeta.value = { total, fetched: collected.length, truncated }
        // Same rationale as the zero-result branch above: mount the results
        // view first, then push data into it.
        firstEntry.value = false
        await nextTick()
        handleTreeClick({ level: 1 })
        if (truncated)
          ElMessage.warning(
            t('search.warn.truncated').replace('{n}', String(MAX_FETCH))
          )
      })
      .catch(err => console.error(err))
      .finally(() => loading && loading.close())
    // Strip DSL syntax (field tags, quotes, year ranges, operators…) before
    // asking the LLM for related keywords. ``baseParams.q`` is exactly the
    // free-text topic the splitter already hoisted out of the user expression
    // (see ``buildBaseQuery`` above); falling back to the raw query mirrors
    // the same fallback we use for the backend ``q`` parameter so the
    // suggestion is always grounded in real topic words rather than syntax
    // noise like ``AU="..."`` or ``PY=2023-2026``.
    //
    // Prefer ``originalTopic`` (captured in ``handleAiSearchPick`` and on
    // direct text searches) over ``baseParams.q`` so that a query string
    // like ``time series agent OR (...)`` doesn't get fed back into the
    // LLM as the seed prompt — which used to return offline-RL drift.
    const suggestSeed =
      (originalTopic.value && originalTopic.value.trim()) ||
      (typeof baseParams.q === 'string' && baseParams.q.trim()
        ? baseParams.q.trim()
        : '')
    if (suggestSeed) {
      guessLoading.value = true
      guessList.value = []
      guessProviderLabel.value = ''
      const payload = toApiPayload(loadAiSettings(), loadApiKey())
      suggestKeywordsWithSettings(suggestSeed, payload)
        .then(res => {
          guessList.value = res.keywords || []
          if (res.provider && res.model)
            guessProviderLabel.value = t('guess.provider', {
              provider: `${res.provider} · ${res.model}`,
              ms: String(res.timecost_ms ?? 0)
            })
        })
        .catch(err => console.error(err))
        .finally(() => {
          guessLoading.value = false
        })
    }
  }

  // Accept ``?q=...`` from the Advanced Search page (or a shared link) and
  // run the search immediately so users land on results directly. Treat the
  // incoming query as the original topic so the right panel re-grounds on
  // it instead of any URL-derived noise.
  const consumeQueryParam = (r: RouteLocationNormalizedLoaded): void => {
    const q = r.query.q
    if (typeof q === 'string' && q.trim()) {
      searchContent.query = q
      originalTopic.value = q
      search()
    }
  }

  const initConfs = async (): Promise<void> => {
    try {
      const res = await listConfs()
      const names = (res.items || []).map(c => c.name)
      availableConfs.value = names
      if (searchContent.confs.length === 0) searchContent.confs = [...names]
    } catch (err) {
      console.error('Failed to load confs', err)
    }
  }

  return {
    firstEntry,
    availableConfs,
    searchContent,
    queryResult,
    searchMeta,
    activeAst,
    originalTopic,
    guessLoading,
    guessList,
    guessProviderLabel,
    search,
    consumeQueryParam,
    initConfs,
    handleTreeClick,
    groupByConfYear,
    setSearchResultRef
  }
}

export default useHomeSearch
