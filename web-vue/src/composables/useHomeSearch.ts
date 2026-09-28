import { nextTick, reactive, ref, shallowRef, type Ref } from 'vue'
import type { RouteLocationNormalizedLoaded } from 'vue-router'
import { ElMessage, ElLoading } from 'element-plus'
import SearchResultList from '@/components/SearchResultList.vue'
import { listConfs, searchPapers, type PaperItem } from '@/api/paper'
import { suggestKeywordsWithSettings } from '@/api/ai'
import { loadAiSettings, loadApiKey, toApiPayload } from '@/utils/aiSettings'
import { useI18n } from '@/utils/i18n'
// prettier-ignore
import { parseDsl, splitForBackend, normalizeQueryInput, collectTextTerms, type AstNode } from '@/utils/queryDsl'
const MAX_FETCH = 5000
const PAGE_SIZE = 200
type SearchMeta = { total: number; fetched: number; truncated: boolean }
type SearchResultRef = InstanceType<typeof SearchResultList> | null
type TreeSelection = {
  level: number
  key?: string
  parent?: string
}
export function useHomeSearch(options?: { suggest?: boolean }) {
  // ``suggest`` defaults on so the home page keeps its AI keyword sidebar;
  // the advanced-search page disables it — feeding a DSL expression into the
  // LLM as a topic seed produces noise, and that page has no guess panel.
  const suggestEnabled = options?.suggest ?? true
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
  let activeTreeSelection: TreeSelection = { level: 1 }
  let activeSearchController: AbortController | null = null
  let activeLoadingClose: (() => void) | null = null
  let searchSequence = 0
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
      // Prefer ``originalTopic`` (the user's pre-OR-merge seed); collect the
      // actual text terms from its AST rather than regex-cleaning the string
      // — cleaning leaves field tags (``TS=``) in ``q`` and ANDs them into a
      // guaranteed-zero substring filter (e.g. the builder's ``TS=a OR TS=b``
      // became ``q="TS=a TS=b"``). ``collectTextTerms`` strips the tags and
      // skips NOT / author / conf / year leaves. Falls back to the cleaned
      // raw query when no text terms can be collected (e.g. a brand-new
      // direct text search that happens to use OR).
      const clean = (s: string): string =>
        s
          .replace(/[()"]/g, ' ')
          .replace(/\s+OR\s+/gi, ' ')
          .replace(/\s+/g, ' ')
          .trim()
      const seedText = originalTopic.value.trim() || rawQuery
      const seedTerms: string[] = []
      collectTextTerms(parseDsl(normalizeQueryInput(seedText)), seedTerms)
      const seed = seedTerms.join(' ') || clean(seedText)
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

  const handleTreeClick = (data: TreeSelection): void => {
    activeTreeSelection = data
    const r = externalRef?.value
    if (r) (r as any).filterResult(queryResult.value, data)
  }

  const updateVisibleResult = (): void => {
    const r = externalRef?.value
    if (r) (r as any).updateResult(queryResult.value, activeTreeSelection)
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
    activeSearchController?.abort()
    activeLoadingClose?.()
    const controller = new AbortController()
    activeSearchController = controller
    const searchId = ++searchSequence
    const isActive = (): boolean =>
      searchId === searchSequence && !controller.signal.aborted

    const loading = ElLoading.service({
      lock: true,
      text: t('search.button') + '...'
    })
    let loadingClosed = false
    const closeLoading = (): void => {
      if (loadingClosed) return
      loadingClosed = true
      loading.close()
      if (activeLoadingClose === closeLoading) activeLoadingClose = null
    }
    activeLoadingClose = closeLoading
    queryResult.value = {}
    searchMeta.value = { total: 0, fetched: 0, truncated: false }
    guessList.value = []
    guessLoading.value = false
    activeTreeSelection = { level: 1 }
    const baseParams = buildBaseQuery()
    // Fetch a real first page immediately (rather than probing with size=1),
    // render it as soon as it arrives, then extend the local facet/export set
    // in the background. Broad terms can match tens of thousands of papers;
    // waiting for all 5,000 candidates used to leave the full-screen loader
    // up for over a minute and made a healthy search look empty.
    const runSearch = async (): Promise<void> => {
      try {
        const first = await searchPapers(
          { ...baseParams, page: 1, size: PAGE_SIZE },
          controller.signal
        )
        if (!isActive()) return

        const total = first.meta?.total ?? 0
        const target = Math.min(total, MAX_FETCH)
        const truncated = total > MAX_FETCH
        const collected = (first.items || []).slice(0, target)

        queryResult.value = groupByConfYear(collected)
        searchMeta.value = { total, fetched: collected.length, truncated }
        // Mount the result view before invoking its exposed list updater.
        firstEntry.value = false
        await nextTick()
        if (!isActive()) return
        handleTreeClick({ level: 1 })
        closeLoading()

        if (collected.length >= target) {
          if (truncated)
            ElMessage.warning(
              t('search.warn.truncated').replace('{n}', String(MAX_FETCH))
            )
          return
        }

        const pages = Math.ceil(target / PAGE_SIZE)
        for (let page = 2; page <= pages; page += 1) {
          const remaining = target - collected.length
          const pageSize = Math.min(PAGE_SIZE, remaining)
          const resp = await searchPapers(
            { ...baseParams, page, size: pageSize },
            controller.signal
          )
          if (!isActive()) return
          const items = resp.items || []
          collected.push(...items)
          queryResult.value = groupByConfYear(collected)
          searchMeta.value = { total, fetched: collected.length, truncated }
          updateVisibleResult()
          if (items.length < pageSize) break
        }

        if (isActive() && truncated)
          ElMessage.warning(
            t('search.warn.truncated').replace('{n}', String(MAX_FETCH))
          )
      } catch (err) {
        if (isActive()) console.error(err)
      } finally {
        if (isActive()) closeLoading()
      }
    }
    void runSearch()
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
    if (suggestEnabled && suggestSeed) {
      guessLoading.value = true
      guessList.value = []
      guessProviderLabel.value = ''
      const payload = toApiPayload(loadAiSettings(), loadApiKey())
      suggestKeywordsWithSettings(suggestSeed, payload)
        .then(res => {
          if (!isActive()) return
          guessList.value = res.keywords || []
          if (res.provider && res.model)
            guessProviderLabel.value = t('guess.provider', {
              provider: `${res.provider} · ${res.model}`,
              ms: String(res.timecost_ms ?? 0)
            })
        })
        .catch(err => console.error(err))
        .finally(() => {
          if (isActive()) guessLoading.value = false
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
