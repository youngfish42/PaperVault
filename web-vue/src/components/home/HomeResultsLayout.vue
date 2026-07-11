<script setup lang="ts">
import { computed, ref } from 'vue'
import ConfsTree from '@/components/ConfsTree.vue'
import SearchResultList from '@/components/SearchResultList.vue'
import AiSuggestPanel from '@/components/AiSuggestPanel.vue'
import { useI18n } from '@/utils/i18n'
import type { PaperItem } from '@/api/paper'
import type { AstNode } from '@/utils/queryDsl'

const props = defineProps<{
  query: string
  refineInResults: boolean
  refineKeyword: string
  queryResult: Record<string, Record<string, PaperItem[]>>
  searchMeta: { total: number; fetched: number; truncated: boolean }
  activeAst: AstNode
  guessList: string[]
  guessLoading: boolean
  guessProviderLabel: string
  isDark: boolean
}>()

const emit = defineEmits<{
  (e: 'update:query', v: string): void
  (e: 'update:refine-in-results', v: boolean): void
  (e: 'update:refine-keyword', v: string): void
  (e: 'top-enter'): void
  (e: 'top-action'): void
  (e: 'apply-refine'): void
  (e: 'toggle-refine-mode', v: string | number | boolean): void
  (e: 'search-author', v: string): void
  (
    e: 'tree-click',
    data: { level: number; key?: string; parent?: string }
  ): void
  (e: 'guess-replace', v: string): void
  (e: 'guess-many', v: string[]): void
  (e: 'toggle-dark'): void
  (e: 'toggle-lang'): void
  (e: 'go-advanced'): void
  (e: 'go-settings'): void
}>()

const { t } = useI18n()

const queryModel = computed({
  get: () => props.query,
  set: (v: string) => emit('update:query', v)
})
const refineInResultsModel = computed({
  get: () => props.refineInResults,
  set: (v: boolean) => emit('update:refine-in-results', v)
})
const refineKeywordModel = computed({
  get: () => props.refineKeyword,
  set: (v: string) => emit('update:refine-keyword', v)
})

const searchResult = ref<InstanceType<typeof SearchResultList> | null>(null)

defineExpose({ searchResult })
</script>

<template>
  <div>
    <header class="pv-topbar">
      <div class="pv-container pv-topbar-inner">
        <router-link to="/" class="brand">{{ t('app.title') }}</router-link>
        <el-input
          v-if="!refineInResultsModel"
          v-model="queryModel"
          :placeholder="t('search.placeholder')"
          clearable
          @keyup.enter="emit('top-enter')"
          size="default"
          class="pv-topbar-input"
        >
          <template #append>
            <el-button icon="Search" @click="emit('top-action')" />
          </template>
        </el-input>
        <el-input
          v-else
          v-model="refineKeywordModel"
          :placeholder="t('search.refinePlaceholder')"
          clearable
          @keyup.enter="emit('top-enter')"
          @input="emit('apply-refine')"
          @clear="emit('apply-refine')"
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
            <el-button icon="Search" @click="emit('top-action')" />
          </template>
        </el-input>
        <el-tooltip
          :content="
            refineInResultsModel
              ? t('search.toggle.onTip')
              : t('search.toggle.offTip')
          "
          placement="bottom"
        >
          <div class="pv-refine-toggle">
            <el-switch
              v-model="refineInResultsModel"
              size="default"
              @change="emit('toggle-refine-mode', $event)"
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
          <el-link type="primary" icon="Tools" @click="emit('go-settings')">
            {{ t('toolbar.settings') }}
          </el-link>
          <el-link type="primary" icon="Operation" @click="emit('go-advanced')">
            {{ t('toolbar.advanced') }}
          </el-link>
          <el-link
            type="primary"
            :icon="isDark ? 'Sunny' : 'Moon'"
            @click="emit('toggle-dark')"
          >
            {{ isDark ? t('toolbar.light') : t('toolbar.dark') }}
          </el-link>
          <el-link
            type="primary"
            icon="ChatLineRound"
            @click="emit('toggle-lang')"
          >
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
          @click="(data: { level: number; key?: string; parent?: string }) => emit('tree-click', data)"
        />
      </aside>
      <div class="pv-center">
        <SearchResultList
          ref="searchResult"
          :ast="activeAst"
          @search-author="(v: string) => emit('search-author', v)"
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
          @pick-many="(v: string[]) => emit('guess-many', v)"
          @replace="(v: string) => emit('guess-replace', v)"
        />
      </aside>
    </section>
  </div>
</template>

<style scoped>
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

.pv-dsl-tip :deep(.pv-syntax-title) {
  font-size: 14px;
  font-weight: 700;
  color: var(--el-text-color-primary, #303133);
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.pv-dsl-tip :deep(.pv-syntax-section) {
  margin-bottom: 14px;
}
.pv-dsl-tip :deep(.pv-syntax-section:last-child) {
  margin-bottom: 0;
}
.pv-dsl-tip :deep(.pv-syntax-section-title) {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-primary, #6f5ed3);
  margin-bottom: 6px;
}
.pv-dsl-tip :deep(.pv-syntax-section-desc) {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  margin-bottom: 8px;
  line-height: 1.6;
}
.pv-dsl-tip :deep(.pv-syntax-table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
  background: var(--el-fill-color-lighter, #fafbfc);
  border-radius: 6px;
  overflow: hidden;
}
.pv-dsl-tip :deep(.pv-syntax-table th) {
  text-align: left;
  padding: 6px 12px;
  background: var(--el-fill-color, #f0f2f5);
  font-weight: 600;
  color: var(--el-text-color-regular, #606266);
  font-size: 12px;
}
.pv-dsl-tip :deep(.pv-syntax-table td) {
  padding: 6px 12px;
  border-top: 1px solid var(--el-border-color-lighter, #ebeef5);
  vertical-align: middle;
}
.pv-dsl-tip :deep(.pv-syntax-chip-row) {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
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
.pv-dsl-tip :deep(.pv-syntax-chip--muted) {
  background: var(--el-fill-color, #f0f2f5);
  color: var(--el-text-color-secondary, #909399);
}
.pv-dsl-tip :deep(.pv-syntax-grid) {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 16px;
  font-size: 12.5px;
}
.pv-dsl-tip :deep(.pv-syntax-grid > div) {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pv-dsl-tip :deep(.pv-syntax-key) {
  flex-shrink: 0;
  width: 64px;
  font-size: 11.5px;
  color: var(--el-text-color-secondary, #909399);
  font-weight: 500;
}
.pv-dsl-tip :deep(.pv-syntax-example) {
  margin-top: 12px;
  padding: 10px 12px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  border-left: 3px solid var(--el-color-primary, #6f5ed3);
  border-radius: 4px;
}
.pv-dsl-tip :deep(.pv-syntax-example-label) {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--el-color-primary, #6f5ed3);
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.pv-dsl-tip :deep(.pv-syntax-example code) {
  background: transparent !important;
  color: var(--el-text-color-primary, #303133) !important;
  padding: 0 !important;
  font-size: 12.5px !important;
  word-break: break-all;
}
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
