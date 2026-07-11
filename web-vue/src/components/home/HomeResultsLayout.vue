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
            <div
              class="pv-dsl-tip pv-syntax-scope"
              v-html="t('search.dslTipHtml')"
            />
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

.pv-dsl-tip {
  max-width: 460px;
}
</style>
