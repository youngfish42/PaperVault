<script setup lang="ts">
import { nextTick, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDark, useToggle } from '@vueuse/core'
import { ElMessage, ElLoading } from 'element-plus'
import ConfsTree from '@/components/ConfsTree.vue'
import SearchResultList from '@/components/SearchResultList.vue'
import AiSuggestPanel from '@/components/AiSuggestPanel.vue'
import AiSearchDialog from '@/components/AiSearchDialog.vue'
import MainNavBar from '@/components/MainNavBar.vue'
import { listConfs, searchPapers, type PaperItem } from '@/api/paper'
import { suggestKeywordsWithSettings } from '@/api/ai'
import { loadAiSettings, loadApiKey, toApiPayload } from '@/utils/aiSettings'
import { buildOrMerge } from '@/utils/queryMerge'
import { useI18n } from '@/utils/i18n'
import {
  parseDsl,
  splitForBackend,
  normalizeQueryInput,
  type AstNode
} from '@/utils/queryDsl'

const { t, toggle: toggleLang } = useI18n()
const route = useRoute()
const router = useRouter()

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

const queryResult = shallowRef<Record<string, Record<string, PaperItem[]>>>({})

const MAX_FETCH = 5000
const PAGE_SIZE = 200

const refineInResults = ref(false)
const refineKeyword = ref('')

interface SearchMeta {
  total: number
  fetched: number
  truncated: boolean
}
const searchMeta = ref<SearchMeta>({ total: 0, fetched: 0, truncated: false })

const searchResult = ref<InstanceType<typeof SearchResultList> | null>(null)

const guessLoading = ref(false)
const guessList = ref<string[]>([])
const guessProviderLabel = ref('')

const aiDialogOpen = ref(false)

// Pinned at the moment the user issues a search so the right-sidebar AI
// "guess" panel can be re-fed the *original* topic on every subsequent
// search, instead of a query that already contains an OR-merged keyword
// list from a previous AI dialog pick. Without this, the OR-merged string
// is sent straight back into the LLM as a topic prompt, which destroys
// keyword quality ("time series agent" + the merged words are themselves
// the prompt the LLM sees).
const originalTopic = ref('')

const cheatsheetOpen = ref(false)
const toggleCheatsheet = (): void => {
  cheatsheetOpen.value = !cheatsheetOpen.value
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

const activeAst = shallowRef<AstNode>({ kind: 'empty' })

const buildBaseQuery = () => {
  // ``field`` is intentionally omitted: the backend defaults to a
  // multi-field topic search (title + author + abstract). The DSL splitter
  // hoists any explicit author / venue / year qualifiers from the user's
  // expression below, and the residual AST is re-applied client-side.
  const params: Record<string, unknown> = {
    sort: '-year'
  }
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
    const cleaned = (s: string): string =>
      s
        .replace(/[()"]/g, ' ')
        .replace(/\s+OR\s+/gi, ' ')
        .replace(/\s+/g, ' ')
        .trim()
    const seed = cleaned(originalTopic.value) || cleaned(rawQuery)
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
  if (!/\s+OR\s+/i.test(searchContent.query)) {
    originalTopic.value = searchContent.query.trim()
  }
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
      if (truncated) {
        ElMessage.warning(
          t('search.warn.truncated').replace('{n}', String(MAX_FETCH))
        )
      }
    })
    .catch(err => {
      console.error(err)
    })
    .finally(() => {
      loading && loading.close()
    })

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
        if (res.provider && res.model) {
          guessProviderLabel.value = t('guess.provider', {
            provider: `${res.provider} · ${res.model}`,
            ms: String(res.timecost_ms ?? 0)
          })
        }
      })
      .catch(err => {
        console.error(err)
      })
      .finally(() => {
        guessLoading.value = false
      })
  }
}

const handleSearchAuthor = (data: string): void => {
  // Surface the click-an-author shortcut as a proper DSL expression so the
  // search box stays in sync with what is actually being executed.
  searchContent.query = `AU="${data}"`
  searchContent.sp_author = ''
  refineInResults.value = false
  refineKeyword.value = ''
  searchResult.value?.clearRefineKeyword?.()
  search()
}

const applyRefineFromTop = (): void => {
  searchResult.value?.setRefineKeyword?.(refineKeyword.value)
}

const handleTopEnter = (): void => {
  if (refineInResults.value) {
    applyRefineFromTop()
  } else {
    search()
  }
}

const handleTopAction = (): void => {
  handleTopEnter()
}

const onToggleRefineMode = (val: string | number | boolean): void => {
  if (!val) {
    refineKeyword.value = ''
    searchResult.value?.clearRefineKeyword?.()
  }
}

const handleSearchGuess = (data: string): void => {
  searchContent.query = data
  // Treat a single-click "search this only" replacement as a fresh topic
  // so the right panel re-grounds against the new query rather than the
  // previous one.
  originalTopic.value = data
  search()
}

const handleSearchGuessMany = (picked: string[]): void => {
  if (!picked.length) return
  searchContent.query = buildOrMerge(searchContent.query, picked)
  search()
}

const handleAiSearchPick = (payload: {
  query: string
  rerank: boolean
  seed: string
}): void => {
  // P3-B wires the OR-merged query from the hero dialog. P3-C will also
  // honour the ``rerank`` flag here to drive the new ``aiRerankEnabled``
  // toggle; for now we only persist the query and re-run.
  //
  // The dialog hands back the *original* seed (the free-text topic the
  // user typed into the dialog before any keyword merge) so we can pin
  // ``originalTopic`` to that seed. The right-sidebar Guess panel will
  // then use the seed instead of the OR-merged query on its own
  // suggestion call.
  searchContent.query = payload.query
  originalTopic.value = payload.seed
  aiDialogOpen.value = false
  search()
}

const handleTreeClick = (data: {
  level: number
  key?: string
  parent?: string
}): void => {
  if (searchResult.value) {
    ;(searchResult.value as any).filterResult(queryResult.value, data)
  }
}

const goAdvanced = (): void => {
  router.push({ path: '/advanced' })
}

const goSettings = (): void => {
  router.push({ path: '/settings' })
}

const isDark = useDark()
const toggleDark = useToggle(isDark)

// Accept ``?q=...`` from the Advanced Search page (or a shared link) and
// run the search immediately so users land on results directly. Treat the
// incoming query as the original topic so the right panel re-grounds on
// it instead of any URL-derived noise.
const consumeQueryParam = (): void => {
  const q = route.query.q
  if (typeof q === 'string' && q.trim()) {
    searchContent.query = q
    originalTopic.value = q
    search()
  }
}

watch(
  () => route.query.q,
  () => consumeQueryParam()
)

onMounted(async () => {
  try {
    const res = await listConfs()
    const names = (res.items || []).map(c => c.name)
    availableConfs.value = names
    if (searchContent.confs.length === 0) {
      searchContent.confs = [...names]
    }
  } catch (err) {
    console.error('Failed to load confs', err)
  }
  consumeQueryParam()
})
</script>

<template>
  <main class="full pos-relative">
    <!-- 共享顶栏（Smart / Advanced / Settings）：跨 firstEntry 状态常驻 -->
    <MainNavBar
      active-key="home"
      :is-dark="isDark"
      @toggle-dark="toggleDark()"
    />

    <!-- 首屏：WoS 风格 hero -->
    <section v-if="firstEntry" class="pv-hero">
      <div class="pv-container pv-hero-inner">
        <h1 class="pv-hero-title">
          <a href="/">{{ t('app.title') }}</a>
        </h1>
        <p class="pv-hero-slogan">{{ t('app.slogan') }}</p>

        <!-- 胶囊型主搜索框 -->
        <div class="pv-hero-searchbox">
          <input
            v-model="searchContent.query"
            class="pv-hero-searchbox-input"
            :placeholder="t('search.placeholder.short')"
            @keyup.enter="search"
          />
          <button
            type="button"
            class="pv-hero-searchbox-btn"
            :title="t('search.button')"
            @click="search"
          >
            <el-icon><Search /></el-icon>
          </button>
        </div>

        <!-- AI 搜索入口：和 hero 主搜索框同一行，opt-in 不会改变现有体验 -->
        <div class="pv-hero-ai-row">
          <el-button class="pv-hero-ai-btn" plain @click="aiDialogOpen = true">
            <el-icon><MagicStick /></el-icon>
            <span>{{ t('search.aiSearch.button') }}</span>
          </el-button>
          <span class="pv-hero-ai-hint">{{ t('search.aiSearch.hint') }}</span>
        </div>
        <AiSearchDialog
          v-model:visible="aiDialogOpen"
          @pick="handleAiSearchPick"
        />

        <!-- 搜索框下方的轻量跳转提示（对齐 WoS） -->
        <p class="pv-hero-hint">
          {{ t('search.heroHint')
          }}<router-link to="/advanced" class="pv-hero-hint-link">{{
            t('search.heroHintLink')
          }}</router-link
          >{{ t('search.heroHintTail') }}
        </p>

        <!-- 检索语法：默认折叠为按钮，展开后显示结构化卡片 -->
        <div class="pv-hero-syntax">
          <button
            type="button"
            class="pv-hero-syntax-toggle"
            :aria-expanded="cheatsheetOpen"
            @click="toggleCheatsheet"
          >
            <el-icon><InfoFilled /></el-icon>
            <span>{{
              cheatsheetOpen
                ? t('search.cheatsheetToggle.hide')
                : t('search.cheatsheetToggle.show')
            }}</span>
            <el-icon class="pv-hero-syntax-chevron">
              <ArrowDown v-if="!cheatsheetOpen" />
              <ArrowUp v-else />
            </el-icon>
          </button>
          <transition name="pv-fade">
            <div
              v-if="cheatsheetOpen"
              class="pv-hero-syntax-panel"
              v-html="t('search.dslTipHtml')"
            />
          </transition>
        </div>
      </div>
    </section>

    <!-- 已搜索：紧凑顶栏 + 三栏 -->
    <template v-else>
      <header class="pv-topbar">
        <div class="pv-container pv-topbar-inner">
          <router-link to="/" class="brand">{{ t('app.title') }}</router-link>
          <el-input
            v-if="!refineInResults"
            v-model="searchContent.query"
            :placeholder="t('search.placeholder')"
            clearable
            @keyup.enter="handleTopEnter"
            size="default"
            class="pv-topbar-input"
          >
            <template #append>
              <el-button icon="Search" @click="handleTopAction" />
            </template>
          </el-input>
          <el-input
            v-else
            v-model="refineKeyword"
            :placeholder="t('search.refinePlaceholder')"
            clearable
            @keyup.enter="handleTopEnter"
            @input="applyRefineFromTop"
            @clear="applyRefineFromTop"
            size="default"
            class="pv-topbar-input pv-topbar-input--refine"
          >
            <template #prepend>
              <span class="pv-refine-prepend">
                <el-icon><Filter /></el-icon>
                <span>{{ t('search.refinePrepend') }}</span>
              </span>
            </template>
            <template #append>
              <el-button icon="Search" @click="handleTopAction" />
            </template>
          </el-input>
          <el-tooltip
            :content="
              refineInResults
                ? t('search.toggle.onTip')
                : t('search.toggle.offTip')
            "
            placement="bottom"
          >
            <div class="pv-refine-toggle">
              <el-switch
                v-model="refineInResults"
                size="default"
                @change="onToggleRefineMode"
              />
              <span class="pv-refine-toggle-label">
                {{ t('search.toggle.label') }}
              </span>
            </div>
          </el-tooltip>
          <el-tooltip placement="bottom" :show-after="120">
            <template #content>
              <div class="pv-dsl-tip" v-html="t('search.dslTipHtml')" />
            </template>
            <el-icon class="pv-dsl-info"><InfoFilled /></el-icon>
          </el-tooltip>
          <div class="pv-topbar-actions">
            <el-link type="primary" icon="Tools" @click="goSettings">
              {{ t('toolbar.settings') }}
            </el-link>
            <el-link type="primary" icon="Operation" @click="goAdvanced">
              {{ t('toolbar.advanced') }}
            </el-link>
            <el-link
              type="primary"
              :icon="isDark ? 'Sunny' : 'Moon'"
              @click="toggleDark()"
            >
              {{ isDark ? t('toolbar.light') : t('toolbar.dark') }}
            </el-link>
            <el-link type="primary" icon="ChatLineRound" @click="toggleLang">
              {{ t('toolbar.lang') }}
            </el-link>
            <el-link
              type="primary"
              icon="Link"
              href="https://github.com/youngfish42/PaperVault"
              target="_blank"
            >
              {{ t('toolbar.github') }}
            </el-link>
          </div>
        </div>
      </header>

      <section class="pv-container pv-main">
        <aside class="pv-side pv-side-left">
          <ConfsTree
            :data="queryResult"
            :meta="searchMeta"
            @click="handleTreeClick"
          />
        </aside>
        <div class="pv-center">
          <SearchResultList
            ref="searchResult"
            :ast="activeAst"
            @search-author="handleSearchAuthor"
          />
        </div>
        <aside class="pv-side pv-side-right">
          <AiSuggestPanel
            :keywords="guessList"
            :loading="guessLoading"
            :title="t('guess.header')"
            :provider-label="guessProviderLabel"
            :empty-text="t('guess.empty')"
            :single-replace-text="t('guess.replace')"
            :merge-button-text="t('guess.merge')"
            @pick-many="handleSearchGuessMany"
            @replace="handleSearchGuess"
          />
        </aside>
      </section>
    </template>

    <el-backtop :right="50" :bottom="50" />
  </main>
</template>

<style scoped>
/* ---------- WoS 风格 hero ---------- */
.pv-hero {
  width: 100%;
  min-height: 100%;
  display: flex;
  flex-direction: column;
  background: linear-gradient(
    180deg,
    var(--el-color-primary-light-9, #ecf5ff) 0%,
    var(--el-bg-color, #fff) 100%
  );
  box-sizing: border-box;
}
.pv-hero-tabs {
  background: var(--el-bg-color, #fff);
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.pv-hero-tabs-inner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 6px;
  padding-bottom: 0;
}
.pv-hero-tab {
  position: relative;
  padding: 14px 18px 16px;
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.2px;
  color: var(--el-text-color-secondary, #909399);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: color 0.18s ease;
}
.pv-hero-tab:hover {
  color: var(--el-text-color-primary, #303133);
}
.pv-hero-tab--active {
  color: var(--el-text-color-primary, #303133);
  font-weight: 600;
}
.pv-hero-tab--active::after {
  content: '';
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: -1px;
  height: 3px;
  border-radius: 2px;
  background: var(--el-color-primary, #6f5ed3);
}
.pv-hero-tabs-actions {
  margin-left: auto;
  display: flex;
  gap: 14px;
  align-items: center;
  padding-bottom: 6px;
}

.pv-hero-inner {
  max-width: 860px;
  padding-top: 64px;
  padding-bottom: 80px;
  text-align: center;
}
.pv-hero-title {
  margin: 0 0 6px;
  font-size: 56px;
  letter-spacing: 1px;
  user-select: none;
}
.pv-hero-title a {
  text-decoration: none;
  color: var(--el-text-color-primary, #303133);
}
.pv-hero-title a:hover {
  text-decoration: underline;
}
.pv-hero-slogan {
  margin: 0 0 32px;
  font-size: 20px;
  font-weight: 500;
  color: var(--el-text-color-regular, #606266);
  user-select: none;
}

/* 胶囊型大搜索框 */
.pv-hero-searchbox {
  display: flex;
  align-items: center;
  margin: 0 auto;
  max-width: 720px;
  height: 56px;
  padding: 0 6px 0 24px;
  background: var(--el-bg-color, #fff);
  border: 1.5px solid var(--el-color-primary-light-5, #b3d8ff);
  border-radius: 9999px;
  box-shadow: 0 2px 12px rgba(111, 94, 211, 0.08);
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}
.pv-hero-searchbox:focus-within {
  border-color: var(--el-color-primary, #6f5ed3);
  box-shadow: 0 4px 18px rgba(111, 94, 211, 0.16);
}
.pv-hero-searchbox-input {
  flex: 1 1 auto;
  min-width: 0;
  height: 100%;
  border: none;
  outline: none;
  background: transparent;
  font-size: 16px;
  color: var(--el-text-color-primary, #303133);
  font-style: italic;
}
.pv-hero-searchbox-input::placeholder {
  color: var(--el-text-color-placeholder, #a8abb2);
  font-style: italic;
}
.pv-hero-searchbox-input:not(:placeholder-shown) {
  font-style: normal;
}
.pv-hero-searchbox-btn {
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: none;
  background: var(--el-color-primary, #6f5ed3);
  color: #fff;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  transition: background 0.18s ease, transform 0.18s ease;
}
.pv-hero-searchbox-btn:hover {
  background: var(--el-color-primary-dark-2, #5847c0);
  transform: scale(1.04);
}

/* 搜索框下方的轻量 AI 入口：与 hero hint 同一视觉权重 */
.pv-hero-ai-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin: 10px auto 0;
  max-width: 720px;
}
.pv-hero-ai-btn :deep(.el-icon) {
  margin-right: 6px;
  vertical-align: middle;
}
.pv-hero-ai-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}

.pv-hero-hint {
  margin: 14px 0 32px;
  font-size: 13px;
  color: var(--el-text-color-regular, #606266);
}
.pv-hero-hint-link {
  color: var(--el-color-primary, #6f5ed3);
  font-weight: 600;
  text-decoration: underline;
  cursor: pointer;
}
.pv-hero-hint-link:hover {
  color: var(--el-color-primary-dark-2, #5847c0);
}

/* 折叠式语法说明 */
.pv-hero-syntax {
  max-width: 720px;
  margin: 0 auto;
}
.pv-hero-syntax-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 9999px;
  font-size: 13px;
  color: var(--el-text-color-regular, #606266);
  cursor: pointer;
  transition: all 0.18s ease;
}
.pv-hero-syntax-toggle:hover {
  border-color: var(--el-color-primary, #6f5ed3);
  color: var(--el-color-primary, #6f5ed3);
}
.pv-hero-syntax-toggle .el-icon {
  font-size: 14px;
}
.pv-hero-syntax-chevron {
  font-size: 12px !important;
}
.pv-hero-syntax-panel {
  margin-top: 14px;
  padding: 18px 22px;
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 12px;
  text-align: left;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
}

/* fade 动画 */
.pv-fade-enter-active,
.pv-fade-leave-active {
  transition: opacity 0.2s ease;
}
.pv-fade-enter-from,
.pv-fade-leave-to {
  opacity: 0;
}

/* ---------- 已搜索后顶栏 ---------- */
.pv-topbar {
  position: sticky;
  top: 0;
  z-index: 100;
  background: var(--el-bg-color, #fff);
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.pv-topbar-inner {
  display: flex;
  align-items: center;
  gap: 16px;
  padding-top: 10px;
  padding-bottom: 10px;
}
.brand {
  font-size: 20px;
  font-weight: 700;
  text-decoration: none;
  color: var(--el-text-color-primary, #333);
  white-space: nowrap;
}
.pv-topbar-input {
  flex: 1 1 auto;
  min-width: 320px;
  max-width: 720px;
}
.pv-topbar-input--refine :deep(.el-input-group__prepend) {
  background: var(--el-color-warning-light-9, #fdf6ec);
  color: var(--el-color-warning, #e6a23c);
  font-weight: 600;
}
.pv-refine-prepend {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
}
.pv-refine-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  font-size: 13px;
  color: var(--el-text-color-regular, #4c4d4f);
  user-select: none;
}
.pv-refine-toggle-label {
  white-space: nowrap;
}
.pv-dsl-info {
  font-size: 16px;
  color: var(--el-color-info, #909399);
  cursor: help;
  flex-shrink: 0;
}
.pv-topbar-actions {
  display: flex;
  gap: 14px;
  align-items: center;
  flex-shrink: 0;
}

/* ---------- 三栏主区 ---------- */
.pv-main {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 260px;
  gap: 16px;
  padding-top: 16px;
  padding-bottom: 24px;
  align-items: start;
}
.pv-side {
  position: sticky;
  top: var(--pv-sticky-top);
}
@media (max-width: 1200px) {
  .pv-main {
    grid-template-columns: 220px minmax(0, 1fr);
  }
  .pv-side-right {
    display: none;
  }
}
@media (max-width: 900px) {
  .pv-hero-title {
    font-size: 42px;
  }
  .pv-hero-slogan {
    font-size: 16px;
  }
  .pv-hero-searchbox {
    height: 48px;
    padding-left: 18px;
  }
  .pv-hero-searchbox-btn {
    width: 38px;
    height: 38px;
  }
  .pv-main {
    grid-template-columns: minmax(0, 1fr);
  }
  .pv-side {
    position: static;
    display: none;
  }
  .pv-topbar-inner {
    flex-wrap: wrap;
  }
  .pv-topbar-actions {
    width: 100%;
    justify-content: flex-end;
  }
}

/* ---------- 结构化语法说明（由 i18n 注入的 HTML） ---------- */
.pv-hero-syntax-panel :deep(.pv-syntax-title),
.pv-dsl-tip :deep(.pv-syntax-title) {
  font-size: 14px;
  font-weight: 700;
  color: var(--el-text-color-primary, #303133);
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.pv-hero-syntax-panel :deep(.pv-syntax-section),
.pv-dsl-tip :deep(.pv-syntax-section) {
  margin-bottom: 14px;
}
.pv-hero-syntax-panel :deep(.pv-syntax-section:last-child),
.pv-dsl-tip :deep(.pv-syntax-section:last-child) {
  margin-bottom: 0;
}
.pv-hero-syntax-panel :deep(.pv-syntax-section-title),
.pv-dsl-tip :deep(.pv-syntax-section-title) {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-primary, #6f5ed3);
  margin-bottom: 6px;
}
.pv-hero-syntax-panel :deep(.pv-syntax-section-desc),
.pv-dsl-tip :deep(.pv-syntax-section-desc) {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  margin-bottom: 8px;
  line-height: 1.6;
}
.pv-hero-syntax-panel :deep(.pv-syntax-table),
.pv-dsl-tip :deep(.pv-syntax-table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
  background: var(--el-fill-color-lighter, #fafbfc);
  border-radius: 6px;
  overflow: hidden;
}
.pv-hero-syntax-panel :deep(.pv-syntax-table th),
.pv-dsl-tip :deep(.pv-syntax-table th) {
  text-align: left;
  padding: 6px 12px;
  background: var(--el-fill-color, #f0f2f5);
  font-weight: 600;
  color: var(--el-text-color-regular, #606266);
  font-size: 12px;
}
.pv-hero-syntax-panel :deep(.pv-syntax-table td),
.pv-dsl-tip :deep(.pv-syntax-table td) {
  padding: 6px 12px;
  border-top: 1px solid var(--el-border-color-lighter, #ebeef5);
  vertical-align: middle;
}
.pv-hero-syntax-panel :deep(.pv-syntax-chip-row),
.pv-dsl-tip :deep(.pv-syntax-chip-row) {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.pv-hero-syntax-panel :deep(.pv-syntax-chip),
.pv-dsl-tip :deep(.pv-syntax-chip) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  color: var(--el-color-primary, #6f5ed3);
  border-radius: 9999px;
  font-size: 12px;
  font-weight: 500;
}
.pv-hero-syntax-panel :deep(.pv-syntax-chip--muted),
.pv-dsl-tip :deep(.pv-syntax-chip--muted) {
  background: var(--el-fill-color, #f0f2f5);
  color: var(--el-text-color-secondary, #909399);
}
.pv-hero-syntax-panel :deep(.pv-syntax-grid),
.pv-dsl-tip :deep(.pv-syntax-grid) {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 16px;
  font-size: 12.5px;
}
.pv-hero-syntax-panel :deep(.pv-syntax-grid > div),
.pv-dsl-tip :deep(.pv-syntax-grid > div) {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pv-hero-syntax-panel :deep(.pv-syntax-key),
.pv-dsl-tip :deep(.pv-syntax-key) {
  flex-shrink: 0;
  width: 64px;
  font-size: 11.5px;
  color: var(--el-text-color-secondary, #909399);
  font-weight: 500;
}
.pv-hero-syntax-panel :deep(.pv-syntax-example),
.pv-dsl-tip :deep(.pv-syntax-example) {
  margin-top: 12px;
  padding: 10px 12px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  border-left: 3px solid var(--el-color-primary, #6f5ed3);
  border-radius: 4px;
}
.pv-hero-syntax-panel :deep(.pv-syntax-example-label),
.pv-dsl-tip :deep(.pv-syntax-example-label) {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--el-color-primary, #6f5ed3);
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.pv-hero-syntax-panel :deep(.pv-syntax-example code),
.pv-dsl-tip :deep(.pv-syntax-example code) {
  background: transparent !important;
  color: var(--el-text-color-primary, #303133) !important;
  padding: 0 !important;
  font-size: 12.5px !important;
  word-break: break-all;
}
.pv-hero-syntax-panel :deep(code),
.pv-dsl-tip :deep(code) {
  background: rgba(111, 94, 211, 0.1);
  color: var(--el-color-primary, #6f5ed3);
  padding: 1px 5px;
  border-radius: 3px;
  font-family: 'Fira Code', Consolas, monospace;
  font-size: 12px;
}
.pv-dsl-tip {
  max-width: 460px;
}
</style>
