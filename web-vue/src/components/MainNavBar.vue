<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useI18n } from '@/utils/i18n'

/**
 * Shared top navigation strip. Replaces the per-view inline tab strips
 * that HomeView (`.pv-hero-tabs`) and AdvancedSearchView (`.pv-adv-tabs`)
 * used to bring with them. Adding a 4th tab is a one-file change.
 *
 * Dark mode is intentionally kept as a prop + emit (not owned here) so
 * the active state stays in the parent view, where it can also drive
 * view-local dark-aware widgets without re-reading localStorage.
 */

const GITHUB_URL = 'https://github.com/youngfish42/PaperVault'

const props = defineProps<{
  activeKey: 'home' | 'advanced' | 'settings'
  isDark: boolean
}>()

const emit = defineEmits<{
  'toggle-dark': []
}>()

const router = useRouter()
const { t, toggle: toggleLang } = useI18n()

const goHome = (): void => {
  if (props.activeKey === 'home') return
  router.push({ path: '/' })
}
const goAdvanced = (): void => {
  if (props.activeKey === 'advanced') return
  router.push({ path: '/advanced' })
}
const goSettings = (): void => {
  if (props.activeKey === 'settings') return
  router.push({ path: '/settings' })
}
</script>

<template>
  <nav class="pv-nav">
    <div class="pv-container pv-nav-inner">
      <button type="button" class="brand" @click="goHome">PaperVault</button>
      <button
        class="pv-nav-tab"
        :class="{ 'pv-nav-tab--active': props.activeKey === 'home' }"
        type="button"
        @click="goHome"
      >
        {{ t('search.tab.smart') }}
      </button>
      <button
        class="pv-nav-tab"
        :class="{ 'pv-nav-tab--active': props.activeKey === 'advanced' }"
        type="button"
        @click="goAdvanced"
      >
        {{ t('search.tab.advanced') }}
      </button>
      <button
        class="pv-nav-tab"
        :class="{ 'pv-nav-tab--active': props.activeKey === 'settings' }"
        type="button"
        @click="goSettings"
      >
        {{ t('toolbar.settings') }}
      </button>
      <div class="pv-nav-actions">
        <el-link
          type="primary"
          :icon="props.isDark ? 'Sunny' : 'Moon'"
          @click="emit('toggle-dark')"
        >
          {{ props.isDark ? t('toolbar.light') : t('toolbar.dark') }}
        </el-link>
        <el-link type="primary" icon="ChatLineRound" @click="toggleLang">
          {{ t('toolbar.lang') }}
        </el-link>
        <el-link
          type="primary"
          icon="Link"
          :href="GITHUB_URL"
          target="_blank"
          rel="noopener noreferrer"
        >
          {{ t('toolbar.github') }}
        </el-link>
      </div>
    </div>
  </nav>
</template>

<style scoped>
.pv-nav {
  position: sticky;
  top: 0;
  z-index: 100;
  background: var(--el-bg-color, #fff);
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.pv-nav-inner {
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
  border: none;
  border-right: 1px solid var(--el-border-color-lighter, #ebeef5);
  background: transparent;
  font-family: inherit;
}
.pv-nav-tab {
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
.pv-nav-tab:hover {
  color: var(--el-text-color-primary, #303133);
}
.pv-nav-tab--active {
  color: var(--el-text-color-primary, #303133);
  font-weight: 600;
}
.pv-nav-tab--active::after {
  content: '';
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: -1px;
  height: 3px;
  border-radius: 2px;
  background: var(--el-color-primary, #6f5ed3);
}
.pv-nav-actions {
  margin-left: auto;
  display: flex;
  gap: 14px;
  align-items: center;
  padding-bottom: 6px;
}
@media (max-width: 768px) {
  .brand {
    border-right: none;
    padding-right: 8px;
  }
  .pv-nav-tab {
    padding: 14px 10px 16px;
  }
}
</style>
