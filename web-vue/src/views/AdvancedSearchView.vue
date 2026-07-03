<script setup lang="ts">
import { computed, onMounted, reactive, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { useDark, useToggle } from '@vueuse/core'
import { ElMessage } from 'element-plus'
import MainNavBar from '@/components/MainNavBar.vue'
import { listConfs } from '@/api/paper'
import { useI18n } from '@/utils/i18n'
import { buildDsl, type DslRow } from '@/utils/queryDsl'

const { t } = useI18n()
const router = useRouter()
const isDark = useDark()
const toggleDark = useToggle(isDark)

/**
 * Web of Science-style advanced search:
 *   - Each row is a (field, value) pair joined to the previous one by an
 *     AND/OR/NOT operator.
 *   - A year range row maps to PY=since-until.
 *   - "Search" composes the rows into a DSL string and navigates back to
 *     the home page with the expression pre-filled in the main search box.
 */

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

const availableConfs = shallowRef<string[]>([])
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
}

const composedDsl = computed(() => {
  const compactRows: DslRow[] = rows
    .filter(r => r.value.trim().length > 0)
    .map(r => ({ field: r.field, value: r.value.trim(), op: r.op }))

  // Append the year range as a synthetic AND row so it always narrows.
  const from = yearRange.from.trim()
  const to = yearRange.to.trim()
  if (from && to) {
    compactRows.push({ field: 'year', value: `${from}-${to}`, op: 'AND' })
  } else if (from) {
    compactRows.push({ field: 'year', value: from, op: 'AND' })
  } else if (to) {
    compactRows.push({ field: 'year', value: to, op: 'AND' })
  }

  return buildDsl(compactRows)
})

const runSearch = (): void => {
  const expr = composedDsl.value.trim()
  if (!expr) {
    ElMessage.warning(t('adv.warn.empty'))
    return
  }
  // Hand the composed expression to HomeView via the router query string so
  // a refresh / shareable URL behaves identically.
  router.push({ path: '/', query: { q: expr } })
}

onMounted(async () => {
  try {
    const res = await listConfs()
    availableConfs.value = (res.items || []).map(c => c.name)
  } catch (err) {
    console.error('Failed to load confs', err)
  }
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
      <div class="pv-adv-grid">
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

          <div class="pv-adv-preview">
            <div class="pv-adv-preview-label">{{ t('adv.preview') }}</div>
            <code class="pv-adv-preview-code">{{
              composedDsl || t('adv.previewEmpty')
            }}</code>
          </div>

          <div class="pv-adv-actions">
            <el-button @click="clearAll">{{ t('adv.clear') }}</el-button>
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
    </section>
  </main>
</template>

<style scoped>
.pv-adv-page {
  width: 100%;
  min-height: 100%;
  background: var(--el-bg-color-page, #f5f7fa);
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
  padding-top: 24px;
  padding-bottom: 32px;
}
.pv-adv-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}
@media (max-width: 1024px) {
  .pv-adv-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.pv-adv-card {
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 10px;
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
.pv-adv-preview {
  margin-top: 14px;
  padding: 12px 14px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  border-left: 3px solid var(--el-color-primary, #6f5ed3);
  border-radius: 4px;
}
.pv-adv-preview-label {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--el-color-primary, #6f5ed3);
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.pv-adv-preview-code {
  display: block;
  font-family: 'Fira Code', Consolas, monospace;
  font-size: 13px;
  color: var(--el-text-color-primary, #303133);
  word-break: break-all;
  white-space: pre-wrap;
}
.pv-adv-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
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
