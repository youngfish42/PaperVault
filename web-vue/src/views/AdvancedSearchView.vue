<script setup lang="ts">
import { computed, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDark, useToggle } from '@vueuse/core'
import { ElMessage, ElMessageBox } from 'element-plus'
import MainNavBar from '@/components/MainNavBar.vue'
import ConfsTree from '@/components/ConfsTree.vue'
import SearchResultList from '@/components/SearchResultList.vue'
import { searchPapers } from '@/api/paper'
import {
  createSavedQuery,
  deleteSavedQuery,
  listSavedQueries,
  updateSavedQuery,
  type SavedQuery
} from '@/api/savedQueries'
import { useAuth } from '@/composables/useAuth'
import { useHomeSearch } from '@/composables/useHomeSearch'
import { copyText } from '@/utils/clipboard'
import { useI18n } from '@/utils/i18n'
import {
  buildDsl,
  combineLegCounts,
  dslToCountPlan,
  parseDslToRows,
  type CoarseBackendParams,
  type DslRow
} from '@/utils/queryDsl'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const isDark = useDark()
const toggleDark = useToggle(isDark)
const { isLoggedIn, ensureFetched } = useAuth()

/**
 * Web of Science-style advanced search:
 *   - Each row is a (field, value) pair joined to the previous one by an
 *     AND/OR/NOT operator.
 *   - A year range row maps to PY=since-until.
 *   - "Search" runs the composed DSL in place (sharing the home page's
 *     ``useHomeSearch`` pipeline); the builder collapses into a summary bar
 *     that keeps 收藏此检索式 next to the freshly returned results.
 */

// Results reuse the home search pipeline; the AI keyword sidebar is disabled
// because a DSL expression makes a poor LLM topic seed and this page has no
// guess panel.
const home = useHomeSearch({ suggest: false })
// Destructure so templates auto-unwrap the refs.
const {
  firstEntry,
  queryResult,
  searchMeta,
  activeAst,
  searchContent,
  originalTopic
} = home
const searchResultRef = shallowRef<InstanceType<
  typeof SearchResultList
> | null>(null)
home.setSearchResultRef(searchResultRef)

/** Snapshot of the expression that produced the on-screen results. */
const executedDsl = ref('')
/** After a search the builder folds into a one-line summary bar. */
const builderCollapsed = ref(false)

interface BuilderRow extends DslRow {
  id: number
}

const FIELD_OPTIONS = computed(() => [
  { label: t('adv.field.topic'), value: 'topic' },
  { label: t('adv.field.title'), value: 'title' },
  { label: t('adv.field.abstract'), value: 'abstract' },
  { label: t('adv.field.author'), value: 'author' },
  { label: t('adv.field.conf'), value: 'conf' },
  { label: t('adv.field.year'), value: 'year' }
])

const OP_OPTIONS: { label: string; value: 'AND' | 'OR' | 'NOT' }[] = [
  { label: 'AND', value: 'AND' },
  { label: 'OR', value: 'OR' },
  { label: 'NOT', value: 'NOT' }
]

let rowSeq = 0
const newRow = (
  field: string = 'topic',
  value: string = '',
  op: 'AND' | 'OR' | 'NOT' = 'AND'
): BuilderRow => ({ id: ++rowSeq, field, value, op })

const rows = reactive<BuilderRow[]>([newRow('topic', '')])

const availableConfs = home.availableConfs
const yearRange = reactive({ from: '', to: '' })

const addRow = (): void => {
  rows.push(newRow('topic', '', 'AND'))
}

const removeRow = (idx: number): void => {
  if (rows.length <= 1) {
    rows.splice(0, rows.length, newRow('topic', ''))
    return
  }
  rows.splice(idx, 1)
}

const clearAll = (): void => {
  rows.splice(0, rows.length, newRow('topic', ''))
  yearRange.from = ''
  yearRange.to = ''
  // Clear the expression panel too — even a dirty edit should not survive
  // an explicit "clear everything".
  exprText.value = ''
  // The on-screen results belong to the cleared query: hide them and drop
  // the executed expression from the URL so a refresh starts clean.
  firstEntry.value = true
  executedDsl.value = ''
  builderCollapsed.value = false
  router.replace({ query: {} })
}

/** Split a flat row chain at OR boundaries (a row with op='OR' starts a new leg). */
const splitOrLegs = <T extends DslRow>(list: T[]): T[][] => {
  const legs: T[][] = []
  for (const r of list) {
    if (r.op === 'OR' || legs.length === 0) legs.push([])
    legs[legs.length - 1].push(r)
  }
  return legs
}

const composedDsl = computed(() => {
  const compactRows: DslRow[] = rows
    .filter(r => r.value.trim().length > 0)
    .map(r => ({ field: r.field, value: r.value.trim(), op: r.op }))

  // The year range is a hard AND over the whole expression. A flat chain
  // parses AND tighter than OR, so a single trailing year row would only
  // constrain the LAST leg — distribute it into every OR leg instead:
  // (A OR B) AND py ≡ (A AND py) OR (B AND py).
  const from = yearRange.from.trim()
  const to = yearRange.to.trim()
  const yearValue = from && to ? `${from}-${to}` : from || to
  if (yearValue) {
    const yearRow: DslRow = { field: 'year', value: yearValue, op: 'AND' }
    if (compactRows.some(r => r.op === 'OR')) {
      const distributed: DslRow[] = []
      for (const leg of splitOrLegs(compactRows))
        distributed.push(...leg, yearRow)
      return buildDsl(distributed)
    }
    compactRows.push(yearRow)
  }

  return buildDsl(compactRows)
})

/**
 * Run the composed expression in place: commit any pending text edit, hand
 * the DSL to the shared search pipeline, and fold the builder into the
 * summary bar so the results (and the 收藏此检索式 entry) take the focus.
 */
const runSearch = (): void => {
  commitExpr()
  const expr = composedDsl.value.trim()
  if (!expr) {
    ElMessage.warning(t('adv.warn.empty'))
    return
  }
  executedDsl.value = expr
  searchContent.query = expr
  originalTopic.value = expr
  builderCollapsed.value = true
  // Keep the executed expression in the URL so a refresh / shared link
  // reproduces this exact search (consumeRouteQuery skips our own replace
  // via the executedDsl guard).
  router.replace({ query: { q: expr, run: '1' } })
  home.search()
}

/**
 * Author click on a result row: instead of navigating away, constrain the
 * current conditions by this author and let the user re-run the search here.
 * The flat row chain parses AND tighter than OR, so appending a trailing
 * AND row would only narrow the LAST OR leg. Since (A OR B) AND au is
 * logically (A AND au) OR (B AND au), the author is distributed into every
 * OR leg — that is the only flat-chain encoding of "narrow everything".
 */
const handleSearchAuthor = (author: string): void => {
  builderCollapsed.value = false
  const hasAuthor = (list: BuilderRow[]): boolean =>
    list.some(r => r.field === 'author' && r.value.trim() === author)
  const filled = rows.filter(r => r.value.trim().length > 0)
  if (!filled.some(r => r.op === 'OR')) {
    if (hasAuthor(rows)) return // row already exists — just reveal the builder
    rows.push(newRow('author', author, 'AND'))
  } else {
    // Split the flat chain at OR boundaries and add the author to every leg
    // that lacks it. A leg already carrying this author needs no change;
    // only when EVERY leg has it is the click a no-op. (A global any-row
    // dedupe would silently leave unconstrained legs like the TS=… branch
    // of `AU="X" OR TS=llm`.)
    const next: BuilderRow[] = []
    let changed = false
    for (const leg of splitOrLegs(filled)) {
      for (const r of leg) next.push(newRow(r.field, r.value, r.op))
      if (!hasAuthor(leg)) {
        next.push(newRow('author', author, 'AND'))
        changed = true
      }
    }
    if (!changed) return
    rows.splice(0, rows.length, ...next)
  }
  // The builder now diverges from the executed query: hide the stale
  // results and drop the executed expression from the URL (same contract
  // as loadFavorite / clearAll) so a refresh cannot resurrect the old
  // search over the just-added condition.
  firstEntry.value = true
  executedDsl.value = ''
  router.replace({ query: {} })
  ElMessage.success(t('adv.results.authorAdded'))
}

const copyExecuted = async (): Promise<void> => {
  const expr = executedDsl.value.trim()
  if (!expr) return
  const ok = await copyText(expr)
  if (ok) ElMessage.success(t('adv.copyOk'))
  else ElMessage.warning(t('adv.copyFail'))
}

/** ---------------------------------------------------------------------
 * Text ↔ builder conversion (issue #196): the expression panel below the
 * rows is a live, editable view of the composed DSL. Builder edits sync
 * into the text while it is untouched; once the user edits or pastes
 * text, the panel turns "dirty" and offers 应用到检索条件 / 还原 to push
 * the text back into the rows or discard the edit.
 * ------------------------------------------------------------------- */

const exprText = ref('')

const exprDirty = computed(
  () => exprText.value.trim() !== composedDsl.value.trim()
)

/**
 * Which side the user touched most recently. While the text is dirty the
 * panel and the builder rows can diverge; when search / save later commits
 * the expression, the side edited LAST must win — otherwise the stale side
 * would silently overwrite edits the user just made on the other side.
 */
const lastEdit = ref<'builder' | 'expr'>('builder')

// Builder edits sync into the text only while the text still shows what the
// builder last produced. Comparing against the watcher's old value (not the
// fresh composedDsl) is what tells "user diverged" from "builder moved" —
// reading exprDirty here would always see the already-updated composedDsl.
watch(composedDsl, (v, old) => {
  if (exprText.value.trim() === (old ?? '').trim()) {
    exprText.value = v
  } else {
    // The text is dirty and the builder moved underneath it.
    lastEdit.value = 'builder'
  }
})

const onExprInput = (): void => {
  lastEdit.value = 'expr'
}

/**
 * Replace the builder rows / year range with whatever ``raw`` parses to.
 * AND-joined year rows fold into the dedicated year-range inputs; since
 * ``composedDsl`` distributes the range into every OR leg, exact duplicates
 * of the already-folded range are dropped so a compose → parse round trip
 * is a fixed point. NOT/OR year rows and year rows with a DIFFERENT value
 * stay as builder rows so their semantics survive. Anything the row model
 * cannot express (nested groups, NEAR, leading NOT) degrades to a single
 * topic row holding the original text, with a warning.
 */
const applyDslToBuilder = (raw: string): void => {
  const { rows: parsed, supported } = parseDslToRows(raw)
  yearRange.from = ''
  yearRange.to = ''
  let yearFolded = false
  const next: BuilderRow[] = []
  for (const r of parsed) {
    const joinOp = r.op ?? 'AND'
    if (r.field === 'year' && joinOp === 'AND') {
      const m = r.value.match(/^(\d{4})\s*-\s*(\d{4})$/)
      const single = /^\d{4}$/.test(r.value)
      if (m || single) {
        const f = m ? m[1] : r.value
        const t = m ? m[2] : ''
        if (!yearFolded) {
          yearRange.from = f
          yearRange.to = t
          yearFolded = true
          continue
        }
        // An exact duplicate of the folded range is one of the distributed
        // OR-leg copies — drop it instead of adding a redundant row.
        if (yearRange.from === f && yearRange.to === t) continue
      }
    }
    next.push(newRow(r.field, r.value, joinOp))
  }
  rows.splice(0, rows.length, ...(next.length ? next : [newRow('topic', '')]))
  if (!supported) {
    ElMessage.warning(t('adv.expr.unsupported'))
  }
}

/** Parse the panel text into the builder rows and normalise the text. */
const doApplyExpr = (): void => {
  applyDslToBuilder(exprText.value.trim())
  exprText.value = composedDsl.value
  // The builder now diverges from the executed query: hide the stale
  // results and drop the executed expression from the URL (same contract
  // as loadFavorite / clearAll / handleSearchAuthor) so a refresh cannot
  // resurrect the old search over the just-applied conditions. When this
  // runs through commitExpr → runSearch, the search immediately
  // re-establishes both.
  firstEntry.value = true
  executedDsl.value = ''
  router.replace({ query: {} })
}

/**
 * Explicit 应用到检索条件 click: push the edited / pasted expression back
 * into the builder rows. When the rows were edited after the text, applying
 * would overwrite those newer edits — make that trade explicit first.
 */
const applyExpr = async (): Promise<void> => {
  const text = exprText.value.trim()
  if (!text) {
    ElMessage.warning(t('adv.expr.empty'))
    return
  }
  if (lastEdit.value === 'builder') {
    try {
      await ElMessageBox.confirm(
        t('adv.expr.applyConfirm'),
        t('adv.expr.apply'),
        {
          type: 'warning',
          confirmButtonText: t('fav.confirm'),
          cancelButtonText: t('fav.cancel')
        }
      )
    } catch {
      return // user cancelled
    }
  }
  doApplyExpr()
}

const resetExpr = (): void => {
  exprText.value = composedDsl.value
}

/**
 * Commit any pending text edit before an action (search / save) consumes the
 * expression, so the text the user sees and the query that runs never
 * diverge. When both sides were edited, the most recent side wins: if the
 * builder rows changed after the text, the stale text is discarded (with a
 * notice) instead of overwriting the newer rows. A cleared panel carries no
 * query either, so it likewise falls back to the composed conditions rather
 * than aborting the action. Copy deliberately does NOT go through here — it
 * stays read-only.
 */
const commitExpr = (): void => {
  if (!exprDirty.value) return
  const text = exprText.value.trim()
  if (!text || lastEdit.value === 'builder') {
    // Only notify when something actually remains to search — otherwise the
    // caller's own empty warning suffices and a double toast would fire.
    if (text && composedDsl.value.trim()) {
      ElMessage.info(t('adv.expr.discarded'))
    }
    resetExpr()
    return
  }
  doApplyExpr()
}

/**
 * Copy is a read-only action: it copies the text exactly as shown in the
 * panel and must NOT commit it back into the rows (a dirty text would
 * silently rewrite — or even collapse — the conditions the user built).
 */
const copyDsl = async (): Promise<void> => {
  const expr = exprText.value.trim()
  if (!expr) {
    ElMessage.warning(t('adv.expr.empty'))
    return
  }
  const ok = await copyText(expr)
  if (ok) ElMessage.success(t('adv.copyOk'))
  else ElMessage.warning(t('adv.copyFail'))
}

/** ---------------------------------------------------------------------
 * Saved queries (favorites) — server-side, per logged-in user.
 * ------------------------------------------------------------------- */

const favDrawerVisible = ref(false)
const favList = shallowRef<SavedQuery[]>([])
const favLoading = ref(false)
const favSaveVisible = ref(false)
const favName = ref('')
const favSaving = ref(false)
const favRefreshingId = ref<number | null>(null)

/**
 * Run the DSL against the corpus and return just the (approximate) hit
 * count.
 *
 * The backend does not parse the DSL — ``q`` is a substring AND filter — so
 * the expression is first turned into a count plan (dslToCountPlan, the
 * same split the home search performs). A top-level OR is counted per leg
 * and merged by combineLegCounts: legs carrying a text ``q`` (usually
 * heavily-overlapping synonyms) merge with Math.max, legs on other fields
 * (author / venue / year, barely overlapping) are summed — the decision is
 * per leg, so a mixed plan neither zeroes out synonyms nor inflates them.
 * Neither is exact — the number is an approximation, labelled "约/~" in
 * the UI. A plan with no narrowing params at all (e.g. ``NOT a OR NOT b``)
 * yields null — better "unknown" than the whole-corpus total.
 */
const fetchResultCount = async (dsl: string): Promise<number | null> => {
  const plan = dslToCountPlan(dsl)
  if (plan.kind === 'none') return null
  const countOf = async (
    params: CoarseBackendParams
  ): Promise<number | null> => {
    if (Object.keys(params).length === 0) return null
    try {
      const res = await searchPapers({ ...params, size: 1 })
      return res.meta?.total ?? null
    } catch {
      return null
    }
  }
  if (plan.kind === 'single') return countOf(plan.params)
  const counts = await Promise.all(plan.legs.map(countOf))
  return combineLegCounts(plan.legs, counts)
}

const openSaveDialog = (): void => {
  commitExpr()
  const expr = composedDsl.value.trim()
  if (!expr) {
    ElMessage.warning(t('adv.warn.empty'))
    return
  }
  if (!isLoggedIn.value) {
    ElMessage.warning(t('fav.loginRequired'))
    return
  }
  favName.value = ''
  favSaveVisible.value = true
}

const saveFavorite = async (): Promise<void> => {
  const name = favName.value.trim()
  const dsl = composedDsl.value.trim()
  if (!name) {
    ElMessage.warning(t('fav.nameRequired'))
    return
  }
  favSaving.value = true
  try {
    const total = await fetchResultCount(dsl)
    await createSavedQuery({ name, dsl, last_count: total })
    ElMessage.success(t('fav.saved'))
    favSaveVisible.value = false
    if (favDrawerVisible.value) await loadFavorites()
  } catch {
    // The axios interceptor already surfaced the error toast.
  } finally {
    favSaving.value = false
  }
}

const loadFavorites = async (): Promise<void> => {
  favLoading.value = true
  try {
    const res = await listSavedQueries()
    favList.value = res.items ?? []
  } catch {
    // toasted by the interceptor
  } finally {
    favLoading.value = false
  }
}

const openFavorites = async (): Promise<void> => {
  if (!isLoggedIn.value) {
    ElMessage.warning(t('fav.loginRequired'))
    return
  }
  favDrawerVisible.value = true
  await loadFavorites()
}

const loadFavorite = (item: SavedQuery): void => {
  applyDslToBuilder(item.dsl)
  exprText.value = composedDsl.value
  // The builder now holds a different query than the results on screen —
  // hide the stale results (and their summary bar) until the user re-runs,
  // and drop the executed query from the URL so a refresh does not
  // resurrect it over the just-loaded conditions.
  firstEntry.value = true
  executedDsl.value = ''
  builderCollapsed.value = false
  router.replace({ query: {} })
  favDrawerVisible.value = false
  ElMessage.success(t('fav.loaded'))
}

const copyFavorite = async (item: SavedQuery): Promise<void> => {
  const ok = await copyText(item.dsl)
  if (ok) ElMessage.success(t('adv.copyOk'))
  else ElMessage.warning(t('adv.copyFail'))
}

const refreshFavorite = async (item: SavedQuery): Promise<void> => {
  favRefreshingId.value = item.id
  try {
    const total = await fetchResultCount(item.dsl)
    if (total === null) {
      // A failed count must not wipe a previously recorded one.
      ElMessage.error(t('fav.refreshFailed'))
      return
    }
    const updated = await updateSavedQuery(item.id, { last_count: total })
    favList.value = favList.value.map(x => (x.id === item.id ? updated : x))
    ElMessage.success(t('fav.refreshed', { n: String(total) }))
  } catch {
    // toasted by the interceptor
  } finally {
    favRefreshingId.value = null
  }
}

const renameFavorite = async (item: SavedQuery): Promise<void> => {
  let name: string
  try {
    const res = await ElMessageBox.prompt(
      t('fav.renamePrompt'),
      t('fav.rename'),
      {
        inputValue: item.name,
        confirmButtonText: t('fav.confirm'),
        cancelButtonText: t('fav.cancel')
      }
    )
    name = (res.value ?? '').trim()
  } catch {
    return // user cancelled
  }
  if (!name || name === item.name) return
  try {
    const updated = await updateSavedQuery(item.id, { name })
    favList.value = favList.value.map(x => (x.id === item.id ? updated : x))
    ElMessage.success(t('fav.saved'))
  } catch {
    // toasted by the interceptor
  }
}

const removeFavorite = async (item: SavedQuery): Promise<void> => {
  try {
    await ElMessageBox.confirm(
      t('fav.deleteConfirm', { name: item.name }),
      t('fav.delete'),
      {
        type: 'warning',
        confirmButtonText: t('fav.confirm'),
        cancelButtonText: t('fav.cancel')
      }
    )
  } catch {
    return // user cancelled
  }
  try {
    await deleteSavedQuery(item.id)
    favList.value = favList.value.filter(x => x.id !== item.id)
    ElMessage.success(t('fav.deleted'))
  } catch {
    // toasted by the interceptor
  }
}

/** Render an ISO timestamp from the backend in the user's locale. */
const formatTime = (iso: string): string => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

/**
 * Query hand-off: HomeView and saved-query deep links arrive as
 * /#/advanced?q=... — pre-fill the builder from the text DSL. Links produced
 * by an executed search additionally carry ``run=1``; those re-run the search
 * so a refresh / shared URL reproduces the results. An edit-mode hand-off
 * (no ``run``) clears the param so a later refresh does not resurrect it
 * over the user's edits.
 */
const consumeRouteQuery = (): void => {
  // Only react while actually on this page — the watcher below also fires on
  // the way out, when consuming the param would be wrong.
  if (route.path !== '/advanced') return
  const q = route.query.q
  if (typeof q !== 'string' || !q.trim()) return
  const text = q.trim()
  // Skip our own runSearch URL replace: the results on screen already come
  // from exactly this expression.
  if (text === executedDsl.value) return
  applyDslToBuilder(text)
  exprText.value = composedDsl.value
  // An incoming expression must be visible: expand the builder even when
  // results from a previous search are on screen.
  builderCollapsed.value = false
  if (route.query.run === '1') {
    runSearch()
  } else {
    router.replace({ query: {} })
  }
}

watch(() => route.query.q, consumeRouteQuery)

onMounted(async () => {
  ensureFetched()
  await home.initConfs()
  consumeRouteQuery()
})
</script>

<template>
  <main class="pv-adv-page">
    <!-- 共享 MainNavBar：Smart / Advanced / Settings + 暗色 / 语言 / GitHub -->
    <MainNavBar
      active-key="advanced"
      :is-dark="isDark"
      @toggle-dark="toggleDark()"
    />

    <section class="pv-container pv-adv-body">
      <!-- 搜索后的摘要条：执行时的检索式快照 + 结果数 + 收藏/复制/修改入口 -->
      <div v-if="!firstEntry && builderCollapsed" class="pv-adv-summary">
        <div class="pv-adv-summary-main">
          <span class="pv-adv-summary-label">{{
            t('adv.executed.label')
          }}</span>
          <code class="pv-adv-summary-dsl" :title="executedDsl">{{
            executedDsl
          }}</code>
          <el-tag size="small" type="info" effect="plain">
            {{ t('fav.count', { n: searchMeta.total }) }}
          </el-tag>
        </div>
        <div class="pv-adv-summary-actions">
          <el-tooltip :content="t('fav.saveTip')" placement="top">
            <el-button size="small" icon="Star" @click="openSaveDialog">
              {{ t('fav.save') }}
            </el-button>
          </el-tooltip>
          <el-button size="small" icon="CopyDocument" @click="copyExecuted">
            {{ t('adv.copy') }}
          </el-button>
          <el-button
            size="small"
            type="primary"
            plain
            icon="Edit"
            @click="builderCollapsed = false"
          >
            {{ t('adv.results.modify') }}
          </el-button>
        </div>
      </div>

      <div v-if="firstEntry || !builderCollapsed" class="pv-adv-grid">
        <el-card shadow="never" class="pv-adv-card pv-adv-card--builder">
          <template #header>
            <div class="pv-adv-card-header">
              <h2 class="pv-adv-card-title">{{ t('adv.pageTitle') }}</h2>
              <span class="pv-adv-card-hint">{{ t('adv.builder.hint') }}</span>
            </div>
          </template>

          <div class="pv-adv-rows">
            <div v-for="(row, idx) in rows" :key="row.id" class="pv-adv-row">
              <div class="pv-adv-row-op">
                <el-select
                  v-if="idx > 0"
                  v-model="row.op"
                  size="default"
                  style="width: 84px"
                >
                  <el-option
                    v-for="o in OP_OPTIONS"
                    :key="o.value"
                    :label="o.label"
                    :value="o.value"
                  />
                </el-select>
                <span v-else class="pv-adv-row-op-placeholder">
                  {{ t('adv.builder.firstRow') }}
                </span>
              </div>
              <el-select
                v-model="row.field"
                size="default"
                class="pv-adv-row-field"
              >
                <el-option
                  v-for="f in FIELD_OPTIONS"
                  :key="f.value"
                  :label="f.label"
                  :value="f.value"
                />
              </el-select>
              <el-input
                v-model="row.value"
                :placeholder="t('adv.builder.valuePh')"
                size="default"
                class="pv-adv-row-value"
                clearable
                @keyup.enter="runSearch"
              />
              <div class="pv-adv-row-ops">
                <el-button
                  size="default"
                  circle
                  icon="Plus"
                  @click="addRow"
                  :title="t('adv.builder.addRow')"
                />
                <el-button
                  size="default"
                  circle
                  icon="Delete"
                  @click="removeRow(idx)"
                  :title="t('adv.builder.removeRow')"
                />
              </div>
            </div>
          </div>

          <div class="pv-adv-year">
            <span class="pv-adv-year-label">{{ t('adv.yearRange') }}</span>
            <el-input
              v-model="yearRange.from"
              size="default"
              :placeholder="t('adv.yearFromPh')"
              style="width: 120px"
              clearable
            />
            <span class="pv-adv-year-sep">—</span>
            <el-input
              v-model="yearRange.to"
              size="default"
              :placeholder="t('adv.yearToPh')"
              style="width: 120px"
              clearable
            />
          </div>

          <div class="pv-adv-expr">
            <div class="pv-adv-expr-head">
              <div class="pv-adv-expr-label">{{ t('adv.expr.label') }}</div>
              <el-button size="small" text icon="CopyDocument" @click="copyDsl">
                {{ t('adv.copy') }}
              </el-button>
            </div>
            <div class="pv-adv-expr-hint">{{ t('adv.expr.hint') }}</div>
            <el-input
              v-model="exprText"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 4 }"
              :placeholder="t('adv.expr.placeholder')"
              :aria-label="t('adv.expr.label')"
              class="pv-adv-expr-input"
              @input="onExprInput"
            />
            <div v-if="exprDirty" class="pv-adv-expr-dirty">
              <span class="pv-adv-expr-dirty-text">
                {{ t('adv.expr.dirty') }}
              </span>
              <div class="pv-adv-expr-dirty-actions">
                <el-button size="small" text @click="resetExpr">
                  {{ t('adv.expr.reset') }}
                </el-button>
                <el-button
                  v-if="exprText.trim()"
                  size="small"
                  type="primary"
                  icon="Upload"
                  @click="applyExpr"
                >
                  {{ t('adv.expr.apply') }}
                </el-button>
              </div>
            </div>
          </div>

          <div class="pv-adv-actions">
            <el-button @click="clearAll">{{ t('adv.clear') }}</el-button>
            <el-tooltip :content="t('fav.saveTip')" placement="top">
              <el-button icon="Star" @click="openSaveDialog">
                {{ t('fav.save') }}
              </el-button>
            </el-tooltip>
            <el-button icon="Collection" @click="openFavorites">
              {{ t('fav.list') }}
            </el-button>
            <el-button type="primary" icon="Search" @click="runSearch">
              {{ t('adv.search') }}
            </el-button>
          </div>
        </el-card>

        <el-card shadow="never" class="pv-adv-card pv-adv-cheatsheet">
          <template #header>
            <span class="pv-adv-cheatsheet-title">{{
              t('adv.cheatsheet.title')
            }}</span>
          </template>
          <div class="pv-adv-cheatsheet-body" v-html="t('search.dslTipHtml')" />
          <div class="pv-adv-cheatsheet-confs" v-if="availableConfs.length">
            <div class="pv-adv-cheatsheet-label">
              {{ t('adv.cheatsheet.confs') }}
            </div>
            <div class="pv-adv-cheatsheet-confs-list">
              <el-tag
                v-for="c in availableConfs"
                :key="c"
                size="small"
                type="info"
                effect="plain"
              >
                {{ c }}
              </el-tag>
            </div>
          </div>
        </el-card>
      </div>

      <!-- 原地检索结果：会议树 + 结果列表（复用首页管线，无 AI 侧栏） -->
      <div v-if="!firstEntry" class="pv-adv-results">
        <aside class="pv-adv-results-side">
          <ConfsTree
            :data="queryResult"
            :meta="searchMeta"
            @click="home.handleTreeClick"
          />
        </aside>
        <div class="pv-adv-results-main">
          <SearchResultList
            ref="searchResultRef"
            :ast="activeAst"
            @search-author="handleSearchAuthor"
          />
        </div>
      </div>
    </section>

    <!-- 收藏检索式：命名保存 -->
    <el-dialog
      v-model="favSaveVisible"
      :title="t('fav.saveTitle')"
      width="480px"
    >
      <div class="pv-fav-save">
        <span class="pv-fav-save-label">{{ t('fav.saveWhat') }}</span>
        <code class="pv-fav-save-dsl">{{ composedDsl }}</code>
        <el-input
          v-model="favName"
          :placeholder="t('fav.namePh')"
          maxlength="80"
          show-word-limit
          @keyup.enter="saveFavorite"
        />
      </div>
      <template #footer>
        <el-button @click="favSaveVisible = false">
          {{ t('fav.cancel') }}
        </el-button>
        <el-button type="primary" :loading="favSaving" @click="saveFavorite">
          {{ t('fav.confirm') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 收藏检索式列表 -->
    <el-drawer
      v-model="favDrawerVisible"
      :title="t('fav.list')"
      size="min(520px, 92vw)"
    >
      <div v-loading="favLoading" class="pv-fav-list">
        <el-empty
          v-if="!favLoading && favList.length === 0"
          :description="t('fav.empty')"
        />
        <div v-for="item in favList" :key="item.id" class="pv-fav-item">
          <div class="pv-fav-item-head">
            <span class="pv-fav-item-name" :title="item.name">{{
              item.name
            }}</span>
            <el-tag size="small" type="info" effect="plain">
              {{
                item.last_count === null
                  ? t('fav.countUnknown')
                  : t('fav.count', { n: item.last_count })
              }}
            </el-tag>
          </div>
          <code class="pv-fav-item-dsl">{{ item.dsl }}</code>
          <div class="pv-fav-item-foot">
            <span class="pv-fav-item-time">{{
              t('fav.updatedAt', { time: formatTime(item.updated_at) })
            }}</span>
            <div class="pv-fav-item-ops">
              <el-button
                size="small"
                text
                type="primary"
                icon="Upload"
                @click="loadFavorite(item)"
              >
                {{ t('fav.load') }}
              </el-button>
              <el-button
                size="small"
                text
                icon="CopyDocument"
                @click="copyFavorite(item)"
              >
                {{ t('adv.copy') }}
              </el-button>
              <el-button
                size="small"
                text
                icon="Refresh"
                :loading="favRefreshingId === item.id"
                @click="refreshFavorite(item)"
              >
                {{ t('fav.refresh') }}
              </el-button>
              <el-button
                size="small"
                text
                icon="EditPen"
                @click="renameFavorite(item)"
              >
                {{ t('fav.rename') }}
              </el-button>
              <el-button
                size="small"
                text
                type="danger"
                icon="Delete"
                @click="removeFavorite(item)"
              >
                {{ t('fav.delete') }}
              </el-button>
            </div>
          </div>
        </div>
      </div>
    </el-drawer>
  </main>
</template>

<style scoped>
.pv-adv-page {
  width: 100%;
  min-height: 100%;
  background: var(--pv-page-bg);
  box-sizing: border-box;
}

/* ---------- WoS 风格 tab 栏 ---------- */
.pv-adv-tabs {
  position: sticky;
  top: 0;
  z-index: 100;
  background: var(--el-bg-color, #fff);
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.pv-adv-tabs-inner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 6px;
  padding-bottom: 0;
}
.brand {
  font-size: 20px;
  font-weight: 700;
  text-decoration: none;
  color: var(--el-text-color-primary, #333);
  white-space: nowrap;
  cursor: pointer;
  padding: 8px 18px 14px 0;
  margin-right: 8px;
  border-right: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.pv-adv-tab {
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
.pv-adv-tab:hover {
  color: var(--el-text-color-primary, #303133);
}
.pv-adv-tab--active {
  color: var(--el-text-color-primary, #303133);
  font-weight: 600;
}
.pv-adv-tab--active::after {
  content: '';
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: -1px;
  height: 3px;
  border-radius: 2px;
  background: var(--el-color-primary, #6f5ed3);
}
.pv-adv-tabs-actions {
  margin-left: auto;
  display: flex;
  gap: 14px;
  align-items: center;
  padding-bottom: 6px;
}

/* ---------- 主体双列布局 ---------- */
.pv-adv-body {
  max-width: var(--pv-page-width);
  margin-left: auto;
  margin-right: auto;
  padding-top: 32px;
  padding-bottom: 40px;
}
.pv-adv-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}

.pv-adv-card {
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: var(--pv-card-radius);
  background: var(--el-bg-color, #fff);
}
.pv-adv-card-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}
.pv-adv-card-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0;
  color: var(--el-text-color-primary, #303133);
}
.pv-adv-card-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}
.pv-adv-rows {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pv-adv-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.pv-adv-row-op {
  width: 90px;
  flex-shrink: 0;
}
.pv-adv-row-op-placeholder {
  display: inline-block;
  width: 84px;
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  text-align: center;
}
.pv-adv-row-field {
  width: 140px;
  flex-shrink: 0;
}
.pv-adv-row-value {
  flex: 1 1 280px;
  min-width: 220px;
}
.pv-adv-row-ops {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
.pv-adv-year {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px dashed var(--el-border-color-lighter, #ebeef5);
}
.pv-adv-year-label {
  font-size: 13px;
  color: var(--el-text-color-regular, #4c4d4f);
  font-weight: 500;
}
.pv-adv-year-sep {
  color: var(--el-text-color-secondary, #909399);
}
.pv-adv-expr {
  margin-top: 14px;
  padding: 12px 14px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  border-left: 3px solid var(--el-color-primary, #6f5ed3);
  border-radius: 4px;
}
.pv-adv-expr-label {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--el-color-primary, #6f5ed3);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.pv-adv-expr-hint {
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary, #909399);
  margin-bottom: 8px;
}
.pv-adv-expr-input :deep(.el-textarea__inner) {
  font-family: 'Fira Code', Consolas, monospace;
  font-size: 13px;
  color: var(--el-text-color-primary, #303133);
}
.pv-adv-expr-dirty {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}
.pv-adv-expr-dirty-text {
  font-size: 12px;
  color: var(--el-color-warning, #e6a23c);
}
.pv-adv-expr-dirty-actions {
  display: flex;
  align-items: center;
}
.pv-adv-expr-dirty-actions .el-button + .el-button {
  margin-left: 8px;
}
.pv-adv-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}

/* ---------- 搜索后摘要条 ---------- */
.pv-adv-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 16px;
  margin-bottom: 16px;
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-left: 3px solid var(--el-color-primary, #6f5ed3);
  border-radius: var(--pv-card-radius);
}
.pv-adv-summary-main {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1 1 auto;
}
.pv-adv-summary-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-color-primary, #6f5ed3);
  white-space: nowrap;
}
.pv-adv-summary-dsl {
  font-family: 'Fira Code', Consolas, monospace;
  font-size: 12.5px;
  color: var(--el-text-color-primary, #303133);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.pv-adv-summary-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.pv-adv-summary-actions .el-button + .el-button {
  margin-left: 0;
}

/* ---------- 原地检索结果区 ---------- */
.pv-adv-results {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.pv-adv-results-side {
  position: sticky;
  top: var(--pv-sticky-top);
}
@media (max-width: 900px) {
  .pv-adv-results {
    grid-template-columns: minmax(0, 1fr);
  }
  .pv-adv-results-side {
    position: static;
    display: none;
  }
}

/* ---------- 右侧 cheatsheet ---------- */
.pv-adv-cheatsheet {
  position: sticky;
  top: var(--pv-sticky-top);
}
.pv-adv-cheatsheet-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary, #303133);
}
.pv-adv-cheatsheet-body {
  font-size: 13px;
  line-height: 1.7;
  color: var(--el-text-color-regular, #4c4d4f);
}
.pv-adv-cheatsheet-confs {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px dashed var(--el-border-color-lighter, #ebeef5);
}
.pv-adv-cheatsheet-label {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  margin-bottom: 6px;
}
.pv-adv-cheatsheet-confs-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* ---------- 结构化语法说明（由 i18n 注入的 HTML） ---------- */
.pv-adv-cheatsheet-body :deep(.pv-syntax-title) {
  font-size: 14px;
  font-weight: 700;
  color: var(--el-text-color-primary, #303133);
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-section) {
  margin-bottom: 14px;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-section:last-child) {
  margin-bottom: 0;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-section-title) {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-primary, #6f5ed3);
  margin-bottom: 6px;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-section-desc) {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  margin-bottom: 8px;
  line-height: 1.6;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
  background: var(--el-fill-color-lighter, #fafbfc);
  border-radius: 6px;
  overflow: hidden;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-table th) {
  text-align: left;
  padding: 6px 12px;
  background: var(--el-fill-color, #f0f2f5);
  font-weight: 600;
  color: var(--el-text-color-regular, #606266);
  font-size: 12px;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-table td) {
  padding: 6px 12px;
  border-top: 1px solid var(--el-border-color-lighter, #ebeef5);
  vertical-align: middle;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-chip-row) {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-chip) {
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
.pv-adv-cheatsheet-body :deep(.pv-syntax-chip--muted) {
  background: var(--el-fill-color, #f0f2f5);
  color: var(--el-text-color-secondary, #909399);
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-grid) {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 6px;
  font-size: 12.5px;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-grid > div) {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-key) {
  flex-shrink: 0;
  width: 64px;
  font-size: 11.5px;
  color: var(--el-text-color-secondary, #909399);
  font-weight: 500;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-example) {
  margin-top: 12px;
  padding: 10px 12px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  border-left: 3px solid var(--el-color-primary, #6f5ed3);
  border-radius: 4px;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-example-label) {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--el-color-primary, #6f5ed3);
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.pv-adv-cheatsheet-body :deep(.pv-syntax-example code) {
  background: transparent !important;
  color: var(--el-text-color-primary, #303133) !important;
  padding: 0 !important;
  font-size: 12.5px !important;
  word-break: break-all;
}
.pv-adv-cheatsheet-body :deep(code) {
  background: rgba(111, 94, 211, 0.1);
  color: var(--el-color-primary, #6f5ed3);
  padding: 1px 5px;
  border-radius: 3px;
  font-family: 'Fira Code', Consolas, monospace;
  font-size: 12px;
}

/* ---------- 检索式面板 / 收藏 ---------- */
.pv-adv-expr-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.pv-fav-save {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.pv-fav-save-label {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}
.pv-fav-save-dsl {
  display: block;
  font-family: 'Fira Code', Consolas, monospace;
  font-size: 12.5px;
  padding: 8px 10px;
  background: var(--el-fill-color-lighter, #fafbfc);
  border-radius: 4px;
  word-break: break-all;
  white-space: pre-wrap;
}
.pv-fav-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.pv-fav-item {
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 8px;
  padding: 10px 12px;
}
.pv-fav-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pv-fav-item-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--el-text-color-primary, #303133);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pv-fav-item-dsl {
  display: block;
  margin-top: 6px;
  font-family: 'Fira Code', Consolas, monospace;
  font-size: 12px;
  color: var(--el-text-color-secondary, #606266);
  word-break: break-all;
  white-space: pre-wrap;
}
.pv-fav-item-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}
.pv-fav-item-time {
  font-size: 11px;
  color: var(--el-text-color-placeholder, #a8abb2);
}
.pv-fav-item-ops {
  display: flex;
  flex-wrap: wrap;
}
.pv-fav-item-ops .el-button + .el-button {
  margin-left: 0;
}

@media (max-width: 768px) {
  .pv-adv-row {
    flex-direction: column;
    align-items: stretch;
  }
  .pv-adv-row-op,
  .pv-adv-row-field {
    width: 100%;
  }
  .pv-adv-cheatsheet {
    position: static;
  }
  .brand {
    border-right: none;
    padding-right: 8px;
  }
}
</style>
