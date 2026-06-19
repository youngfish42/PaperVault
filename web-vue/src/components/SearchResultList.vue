<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import FILE from '@/utils/file'
import type { PaperItem } from '@/api/paper'
import { useI18n } from '@/utils/i18n'
import {
  FIELDS,
  getFieldKeyForConf,
  labelOfField,
  labelOfOther,
  OTHER_FIELD_KEY
} from '@/utils/fields'

const { t, lang } = useI18n()

const emits = defineEmits<{
  (e: 'searchAuthor', val: string): void
}>()

type SortKey = 'year-desc' | 'year-asc' | 'conf' | 'title'

const rawList = ref<PaperItem[]>([])
const sortMethod = ref<SortKey>('year-desc')

const refine = reactive({
  keyword: '',
  hasAbstract: false,
  hasCode: false,
  yearRange: [0, 0] as [number, number],
  fields: [] as string[]
})

const yearBounds = computed<[number, number]>(() => {
  if (rawList.value.length === 0) return [0, 0]
  let min = Infinity
  let max = -Infinity
  for (const p of rawList.value) {
    const y = Number(p.year)
    if (!Number.isFinite(y)) continue
    if (y < min) min = y
    if (y > max) max = y
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 0]
  return [min, max]
})

const expandedSet = ref<Set<string>>(new Set())
const expandKey = (it: PaperItem, idx: number): string =>
  it.id || `${it.conf}-${it.year}-${idx}-${it.title}`

const toggleExpand = (key: string): void => {
  const next = new Set(expandedSet.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedSet.value = next
}

const exportFile = (method: string): void => {
  if (method === 'csv') {
    FILE.exportCSV(filteredList.value, 'result.csv')
  } else if (method === 'txt') {
    FILE.exportTxt(filteredList.value, 'result.txt')
  }
}

const deleteResult = (item: PaperItem): void => {
  const idx = rawList.value.indexOf(item)
  if (idx >= 0) {
    rawList.value.splice(idx, 1)
  }
}

const jumpUrl = (url?: string | null) => {
  if (url) window.open(url)
}

const searchAuthor = (author: string): void => {
  emits('searchAuthor', author)
}

const copyText = async (text: string): Promise<void> => {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(t('result.copied'))
  } catch {
    // graceful fallback
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
      ElMessage.success(t('result.copied'))
    } finally {
      document.body.removeChild(ta)
    }
  }
}

const filterResult: (target: any, option: any) => void = (target, option) => {
  let next: PaperItem[] = []
  const { level, key, parent } = option
  if (Number(level) === 1) {
    next = collectAll(target)
  } else if (Number(level) === 2) {
    const group = target?.[key] ?? {}
    for (const k in group) next = next.concat(group[k])
  } else if (Number(level) === 3) {
    next = next.concat(target?.[parent]?.[key] ?? [])
  }
  rawList.value = next
  expandedSet.value = new Set()
  refine.keyword = ''
  refine.hasAbstract = false
  refine.hasCode = false
  refine.fields = []
  const [lo, hi] = yearBounds.value
  refine.yearRange = [lo, hi]
  page.current = 1
}

const collectAll = (target: any): PaperItem[] => {
  const out: PaperItem[] = []
  const walk = (node: any) => {
    if (Array.isArray(node)) {
      out.push(...node)
    } else if (node && typeof node === 'object') {
      for (const k in node) walk(node[k])
    }
  }
  walk(target)
  return out
}

watch(yearBounds, ([lo, hi]) => {
  if (lo === 0 && hi === 0) return
  if (
    refine.yearRange[0] < lo ||
    refine.yearRange[1] > hi ||
    refine.yearRange[0] === 0
  ) {
    refine.yearRange = [lo, hi]
  }
})

const filteredList = computed<PaperItem[]>(() => {
  const kw = refine.keyword.trim().toLowerCase()
  const [lo, hi] = refine.yearRange
  const fieldSet = refine.fields.length > 0 ? new Set(refine.fields) : null
  let list = rawList.value.filter(p => {
    if (refine.hasAbstract && !p.abstract) return false
    if (refine.hasCode && (!p.code || p.code === '#')) return false
    const y = Number(p.year)
    if (Number.isFinite(y) && (lo || hi)) {
      if (y < lo || y > hi) return false
    }
    if (fieldSet) {
      const fk = getFieldKeyForConf(p.conf) || OTHER_FIELD_KEY
      if (!fieldSet.has(fk)) return false
    }
    if (kw) {
      const hay = [
        p.title || '',
        (p.authors || []).join(' '),
        p.abstract || '',
        p.conf || ''
      ]
        .join(' ')
        .toLowerCase()
      if (!hay.includes(kw)) return false
    }
    return true
  })

  list = list.slice()
  switch (sortMethod.value) {
    case 'year-asc':
      list.sort((a, b) => Number(a.year) - Number(b.year))
      break
    case 'conf':
      list.sort((a, b) => {
        const a1 = (a.conf || '').toUpperCase()
        const b1 = (b.conf || '').toUpperCase()
        if (a1 !== b1) return a1 < b1 ? -1 : 1
        return Number(b.year) - Number(a.year)
      })
      break
    case 'title':
      list.sort((a, b) =>
        (a.title || '').localeCompare(b.title || '', undefined, {
          sensitivity: 'base'
        })
      )
      break
    case 'year-desc':
    default:
      list.sort((a, b) => Number(b.year) - Number(a.year))
  }
  return list
})

const resetRefine = (): void => {
  refine.keyword = ''
  refine.hasAbstract = false
  refine.hasCode = false
  refine.fields = []
  const [lo, hi] = yearBounds.value
  refine.yearRange = [lo, hi]
}

type PAGE = { current: number; size: number }
const page: PAGE = reactive({ current: 1, size: 20 })

const pageCurrentChange = (v: number): void => {
  page.current = v
  if (typeof window !== 'undefined') {
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
}
const pageSizeChange = (v: number): void => {
  page.current = 1
  page.size = v
}

watch(
  () => [
    refine.keyword,
    refine.hasAbstract,
    refine.hasCode,
    refine.yearRange[0],
    refine.yearRange[1],
    refine.fields.slice().sort().join('|'),
    sortMethod.value
  ],
  () => {
    page.current = 1
  }
)

const virtualList = computed(() =>
  filteredList.value.slice(
    (page.current - 1) * page.size,
    page.current * page.size
  )
)

const showYearSlider = computed(() => {
  const [lo, hi] = yearBounds.value
  return hi > lo
})

interface FieldFacet {
  key: string
  label: string
  count: number
}

const fieldOptions = computed<FieldFacet[]>(() => {
  if (rawList.value.length === 0) return []
  const counts = new Map<string, number>()
  for (const p of rawList.value) {
    const k = getFieldKeyForConf(p.conf) || OTHER_FIELD_KEY
    counts.set(k, (counts.get(k) ?? 0) + 1)
  }
  const out: FieldFacet[] = []
  for (const f of FIELDS) {
    const c = counts.get(f.key) ?? 0
    if (c > 0) {
      out.push({
        key: f.key,
        label: labelOfField(f.key, lang.value),
        count: c
      })
    }
  }
  const otherCount = counts.get(OTHER_FIELD_KEY) ?? 0
  if (otherCount > 0) {
    out.push({
      key: OTHER_FIELD_KEY,
      label: labelOfOther(lang.value),
      count: otherCount
    })
  }
  out.sort((a, b) => b.count - a.count)
  return out
})

const isRefineDirty = computed<boolean>(
  () =>
    !!refine.keyword ||
    refine.hasAbstract ||
    refine.hasCode ||
    refine.fields.length > 0 ||
    refine.yearRange[0] !== yearBounds.value[0] ||
    refine.yearRange[1] !== yearBounds.value[1]
)

defineExpose({
  filterResult
})
</script>

<template>
  <div class="pv-result-wrap">
    <!-- Refine bar (Semantic-Scholar-style) -->
    <el-card
      v-show="rawList.length > 0"
      class="pv-refine-card pv-flat-card"
      shadow="never"
    >
      <div class="pv-refine-grid">
        <div class="refine-cell refine-search">
          <span class="cell-label">{{ t('result.filter.searchWithin') }}</span>
          <el-input
            v-model="refine.keyword"
            :placeholder="t('result.filter.searchWithinPh')"
            clearable
            size="small"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </div>

        <div class="refine-cell">
          <span class="cell-label">{{ t('result.sortBy') }}</span>
          <el-select v-model="sortMethod" size="small" style="width: 100%">
            <el-option :label="t('result.sort.year')" :value="'year-desc'" />
            <el-option :label="t('result.sort.yearAsc')" :value="'year-asc'" />
            <el-option :label="t('result.sort.conf')" :value="'conf'" />
            <el-option :label="t('result.sort.title')" :value="'title'" />
          </el-select>
        </div>

        <div class="refine-cell refine-toggles">
          <span class="cell-label">{{ t('result.filter.title') }}</span>
          <div class="toggles">
            <el-checkbox v-model="refine.hasAbstract" size="small">
              {{ t('result.filter.hasAbstract') }}
            </el-checkbox>
            <el-checkbox v-model="refine.hasCode" size="small">
              {{ t('result.filter.hasCode') }}
            </el-checkbox>
          </div>
        </div>

        <div v-if="showYearSlider" class="refine-cell refine-year">
          <span class="cell-label">
            {{ t('result.filter.yearRange') }}
            <span class="year-range-text">
              {{ refine.yearRange[0] }} – {{ refine.yearRange[1] }}
            </span>
          </span>
          <el-slider
            v-model="refine.yearRange"
            range
            size="small"
            :min="yearBounds[0]"
            :max="yearBounds[1]"
            :step="1"
            :marks="{
              [yearBounds[0]]: String(yearBounds[0]),
              [yearBounds[1]]: String(yearBounds[1])
            }"
          />
        </div>
      </div>

      <!-- Field facet (mirrors README CATEGORY_MAP from maintain.py) -->
      <div v-if="fieldOptions.length > 0" class="pv-field-facet">
        <span class="cell-label field-label">
          {{ t('result.filter.field') }}
        </span>
        <div class="field-chips">
          <el-check-tag
            :checked="refine.fields.length === 0"
            type="primary"
            @change="refine.fields = []"
          >
            {{ t('result.filter.fieldAll') }}
            <span class="chip-count">({{ rawList.length }})</span>
          </el-check-tag>
          <el-check-tag
            v-for="f in fieldOptions"
            :key="f.key"
            :checked="refine.fields.includes(f.key)"
            type="primary"
            @change="
              checked => {
                if (checked) {
                  if (!refine.fields.includes(f.key)) {
                    refine.fields = [...refine.fields, f.key]
                  }
                } else {
                  refine.fields = refine.fields.filter(k => k !== f.key)
                }
              }
            "
          >
            {{ f.label }}
            <span class="chip-count">({{ f.count }})</span>
          </el-check-tag>
        </div>
      </div>

      <div class="pv-refine-foot">
        <span class="muted">
          {{
            t('result.filter.matched', {
              n: filteredList.length,
              total: rawList.length
            })
          }}
        </span>
        <div class="foot-actions">
          <el-link
            type="primary"
            :underline="false"
            @click="resetRefine"
            v-show="isRefineDirty"
          >
            <el-icon class="el-icon--left"><RefreshLeft /></el-icon>
            {{ t('result.filter.reset') }}
          </el-link>
          <el-link @click="exportFile('txt')" :underline="false">
            <el-icon class="el-icon--left"><Document /></el-icon>
            {{ t('result.export.txt') }}
          </el-link>
          <el-link @click="exportFile('csv')" :underline="false">
            <el-icon class="el-icon--left"><Collection /></el-icon>
            {{ t('result.export.csv') }}
          </el-link>
        </div>
      </div>
    </el-card>

    <!-- Paper list -->
    <ul class="pv-paper-list" v-show="filteredList.length > 0">
      <li
        v-for="(itm, index) in virtualList"
        :key="expandKey(itm, index)"
        class="pv-paper-card"
      >
        <!-- Meta line: venue/year + actions -->
        <div class="pv-meta-row">
          <div class="meta-left">
            <el-tag size="small" type="warning" effect="dark" class="tag-conf">
              {{ itm.conf }}
            </el-tag>
            <el-tag size="small" type="danger" effect="plain" class="tag-year">
              {{ itm.year }}
            </el-tag>
            <el-tag
              v-if="itm.abstract"
              size="small"
              type="success"
              effect="plain"
              class="tag-flag"
            >
              <el-icon><Reading /></el-icon>
              <span>{{ t('result.abstract') }}</span>
            </el-tag>
            <el-tag
              v-if="itm.code && itm.code !== '#'"
              size="small"
              type="info"
              effect="plain"
              class="tag-flag pointer"
              @click="jumpUrl(itm.code)"
            >
              <el-icon><Link /></el-icon>
              <span>{{ t('result.code') }}</span>
            </el-tag>
          </div>
          <div class="meta-right">
            <el-tooltip :content="t('result.copyTitle')" placement="top">
              <el-icon class="meta-icon pointer" @click="copyText(itm.title)">
                <DocumentCopy />
              </el-icon>
            </el-tooltip>
            <el-tooltip :content="t('result.delete')" placement="top">
              <el-icon
                class="meta-icon pointer delete"
                @click="deleteResult(itm)"
              >
                <CloseBold />
              </el-icon>
            </el-tooltip>
          </div>
        </div>

        <!-- Title -->
        <h3 class="paper-title">
          <a
            :href="itm.url ?? undefined"
            target="_blank"
            rel="noopener noreferrer"
          >
            {{ itm.title }}
          </a>
        </h3>

        <!-- Authors -->
        <div class="paper-authors" v-if="itm.authors && itm.authors.length > 0">
          <template
            v-for="(author, authorIndex) in itm.authors"
            :key="authorIndex"
          >
            <el-link
              class="author"
              :underline="false"
              @click="searchAuthor(author)"
            >
              {{ author }}
            </el-link>
            <span
              v-if="authorIndex < (itm.authors?.length ?? 0) - 1"
              class="author-sep"
            >
              ,
            </span>
          </template>
        </div>

        <!-- Abstract preview (inline) -->
        <div v-if="itm.abstract" class="paper-abstract">
          <p
            class="abstract-text"
            :class="{
              clamp: !expandedSet.has(expandKey(itm, index))
            }"
          >
            {{ itm.abstract }}
          </p>
          <el-link
            type="primary"
            :underline="false"
            class="toggle-more"
            @click="toggleExpand(expandKey(itm, index))"
          >
            {{
              expandedSet.has(expandKey(itm, index))
                ? t('result.less')
                : t('result.more')
            }}
            <el-icon class="el-icon--right">
              <ArrowDown v-if="!expandedSet.has(expandKey(itm, index))" />
              <ArrowUp v-else />
            </el-icon>
          </el-link>
        </div>
        <div v-else class="paper-abstract empty">
          <span class="muted">{{ t('result.noAbstract') }}</span>
        </div>
      </li>
    </ul>

    <el-empty v-show="rawList.length <= 0" :description="t('result.empty')" />
    <el-empty
      v-show="rawList.length > 0 && filteredList.length <= 0"
      :description="t('result.empty')"
    />

    <div class="mt-15" v-show="filteredList.length > 0">
      <el-pagination
        class="align-right"
        v-model:current-page="page.current"
        v-model:page-size="page.size"
        :page-sizes="[10, 20, 50, 100, 200]"
        layout="sizes, prev, pager, next, jumper, total"
        :total="filteredList.length"
        @size-change="pageSizeChange"
        @current-change="pageCurrentChange"
      />
    </div>
  </div>
</template>

<style scoped>
.pv-result-wrap {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* === Refine card === */
.pv-refine-card {
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 8px;
}
.pv-refine-card :deep(.el-card__body) {
  padding: 12px 14px;
}
.pv-refine-grid {
  display: grid;
  grid-template-columns: 2fr 1fr 1.4fr 1.6fr;
  gap: 14px 18px;
  align-items: end;
}
.refine-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.cell-label {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  display: flex;
  justify-content: space-between;
  gap: 6px;
}
.year-range-text {
  color: var(--el-color-primary, #409eff);
  font-weight: 600;
}
.refine-toggles .toggles {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  padding: 2px 0;
}
.refine-year :deep(.el-slider) {
  margin: 0 8px;
}
.pv-refine-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--el-border-color-lighter, #ebeef5);
}
.foot-actions {
  display: flex;
  gap: 14px;
  align-items: center;
}
.muted {
  color: var(--el-text-color-secondary, #909399);
  font-size: 13px;
}

@media (max-width: 1100px) {
  .pv-refine-grid {
    grid-template-columns: 1fr 1fr;
  }
}
@media (max-width: 640px) {
  .pv-refine-grid {
    grid-template-columns: 1fr;
  }
  .pv-refine-foot {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}

/* === Field facet row === */
.pv-field-facet {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--el-border-color-lighter, #ebeef5);
  display: flex;
  align-items: flex-start;
  gap: 10px;
  flex-wrap: wrap;
}
.field-label {
  padding-top: 4px;
  flex-shrink: 0;
}
.field-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 8px;
  flex: 1;
  min-width: 0;
}
.field-chips :deep(.el-check-tag) {
  padding: 4px 10px;
  font-size: 12px;
  line-height: 1.2;
}
.chip-count {
  opacity: 0.7;
  margin-left: 4px;
  font-weight: 400;
}

/* === Paper list (Semantic-Scholar-ish card) === */
.pv-paper-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pv-paper-card {
  padding: 14px 16px;
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 8px;
  transition: box-shadow 0.15s, border-color 0.15s;
}
.pv-paper-card:hover {
  border-color: var(--el-color-primary-light-5, #a0cfff);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.pv-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.meta-left {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}
.meta-right {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.tag-flag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.meta-icon {
  color: var(--el-text-color-placeholder, #c0c4cc);
  font-size: 15px;
  transition: color 0.15s;
}
.meta-icon:hover {
  color: var(--el-color-primary, #409eff);
}
.meta-icon.delete:hover {
  color: var(--el-color-danger, #f56c6c);
}

.paper-title {
  margin: 4px 0 6px 0;
  font-size: 16px;
  font-weight: 600;
  line-height: 1.45;
  word-break: break-word;
}
.paper-title a {
  color: var(--el-color-primary, #1d6fda);
  text-decoration: none;
}
.paper-title a:hover {
  text-decoration: underline;
}

.paper-authors {
  font-size: 13px;
  color: var(--el-text-color-regular, #606266);
  line-height: 1.6;
  margin-bottom: 8px;
  word-break: break-word;
}
.author {
  font-size: 13px;
  color: var(--el-text-color-regular, #606266);
}
.author:hover {
  color: var(--el-color-primary, #409eff);
  text-decoration: underline;
}
.author-sep {
  margin: 0 4px 0 0;
  color: var(--el-text-color-placeholder, #c0c4cc);
}

.paper-abstract {
  font-size: 13.5px;
  line-height: 1.6;
  color: var(--el-text-color-regular, #4c4d4f);
}
.paper-abstract.empty {
  font-style: italic;
}
.abstract-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.abstract-text.clamp {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.toggle-more {
  font-size: 12px;
  margin-top: 4px;
}

.pointer {
  cursor: pointer;
}
</style>
