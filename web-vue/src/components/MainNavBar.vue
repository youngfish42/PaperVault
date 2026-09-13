<script setup lang="ts">
import { useRouter } from 'vue-router'
import { ref } from 'vue'
import { onMounted } from 'vue'
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
  activeKey: 'home' | 'advanced' | 'settings' | 'docs'
  isDark: boolean
}>()

const emit = defineEmits<{
  'toggle-dark': []
}>()

const router = useRouter()
const { t, toggle: toggleLang } = useI18n()
const loginVisible = ref(false)
const currentUser = ref<any>(null)
const loadAuth = async (): Promise<void> => {
  try {
    const response = await fetch('/api/v1/auth/me')
    const data = await response.json()
    currentUser.value = data.authenticated ? data : null
  } catch {
    currentUser.value = null
  }
}
const logout = async (): Promise<void> => {
  await fetch('/api/v1/auth/logout', { method: 'POST' })
  currentUser.value = null
}
onMounted(loadAuth)

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
const goDocs = (): void => {
  if (props.activeKey === 'docs') return
  router.push({ path: '/docs' })
}
</script>

<template>
  <nav class="pv-nav">
    <div class="pv-container pv-nav-inner">
      <button type="button" class="brand" @click="goHome">PaperVault</button>
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
      <button
        class="pv-nav-tab"
        :class="{ 'pv-nav-tab--active': props.activeKey === 'docs' }"
        type="button"
        @click="goDocs"
      >
        {{ t('toolbar.docs') }}
      </button>
      <a
        class="pv-nav-github"
        :href="GITHUB_URL"
        target="_blank"
        rel="noopener noreferrer"
        >GitHub</a
      >
      <div class="pv-nav-actions">
        <template v-if="currentUser">
          <el-dropdown>
            <el-button link type="primary">{{
              currentUser.username || currentUser.user?.name || '已登录'
            }}</el-button>
            <template #dropdown
              ><el-dropdown-menu
                ><el-dropdown-item @click="logout"
                  >退出登录</el-dropdown-item
                ></el-dropdown-menu
              ></template
            >
          </el-dropdown>
        </template>
        <el-button v-else link type="primary" @click="loginVisible = true"
          >登录</el-button
        >
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
      </div>
    </div>
  </nav>
  <el-dialog v-model="loginVisible" title="登录 PaperVault" width="360px">
    <div class="login-options">
      <el-button type="primary" tag="a" href="/api/v1/auth/oauth/zhihu"
        >使用知乎登录</el-button
      >
      <el-button tag="a" href="/api/v1/auth/oauth/github"
        >使用 GitHub 登录</el-button
      >
    </div>
  </el-dialog>
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
  min-height: 56px;
  gap: 4px;
  max-width: none;
  padding-left: 32px;
  padding-right: 32px;
}
.brand {
  font-size: 19px;
  font-weight: 700;
  letter-spacing: -0.045em;
  text-decoration: none;
  color: var(--el-text-color-primary, #333);
  white-space: nowrap;
  cursor: pointer;
  padding: 0 24px 0 0;
  margin-right: 10px;
  border: none;
  background: transparent;
  font-family: inherit;
}
.brand b {
  color: inherit;
  font-weight: inherit;
}
.pv-nav-github {
  margin: 0 18px 0 4px;
  padding-left: 16px;
  border-left: 1px solid var(--pv-line);
  color: var(--el-text-color-secondary, #909399);
  font-size: 12px;
  letter-spacing: 0.02em;
  white-space: nowrap;
  text-decoration: none;
}
.pv-nav-github:hover {
  color: var(--el-color-primary, #409eff);
}
.pv-nav-tab {
  position: relative;
  padding: 19px 16px 18px;
  font-size: 14px;
  font-weight: 500;
  color: var(--el-text-color-secondary, #909399);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: color 0.18s ease;
}
.pv-nav-tab:hover,
.pv-nav-tab--active {
  color: var(--el-text-color-primary, #303133);
}
.pv-nav-tab--active {
  font-weight: 600;
}
.pv-nav-tab--active::after {
  content: '';
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: -1px;
  height: 2px;
  border-radius: 2px;
  background: var(--el-color-primary, #6f5ed3);
}
.pv-nav-actions {
  margin-left: auto;
  display: flex;
  gap: 12px;
  align-items: center;
}
.pv-nav-actions :deep(.el-link),
.pv-nav-actions :deep(.el-button) {
  font-size: 13px;
  white-space: nowrap;
}
@media (max-width: 900px) {
  .pv-nav-github {
    display: none;
  }
  .pv-nav-actions :deep(.el-link:nth-last-child(-n + 2)) {
    display: none;
  }
}
@media (max-width: 600px) {
  .pv-nav-inner {
    min-height: 52px;
    padding-left: 16px;
    padding-right: 16px;
  }
  .brand {
    font-size: 17px;
    padding-right: 10px;
    margin-right: 2px;
  }
  .pv-nav-tab {
    padding: 18px 9px 17px;
    font-size: 13px;
  }
  .pv-nav-actions {
    gap: 6px;
  }
  .pv-nav-actions :deep(.el-link),
  .pv-nav-actions :deep(.el-button) {
    font-size: 0;
  }
  .pv-nav-actions :deep(.el-link .el-icon),
  .pv-nav-actions :deep(.el-button .el-icon) {
    font-size: 16px;
    margin: 0;
  }
}
</style>
