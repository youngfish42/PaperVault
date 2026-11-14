<script setup lang="ts">
import { useDark, useToggle } from '@vueuse/core'
import AiSuggestSection from '@/components/AiSuggestSection.vue'
import MainNavBar from '@/components/MainNavBar.vue'
import { useI18n } from '@/utils/i18n'

/**
 * Settings page for PaperVault.
 *
 * Lets users configure the LLM provider, API key, and defaults used by the
 * AI keyword suggestion dialog on the home page, the AI suggestion panel
 * in search results, and AI-powered result reranking. All values are kept
 * in browser storage (localStorage for non-secrets, sessionStorage for the
 * API key) and are never uploaded to our servers.
 */

const isDark = useDark()
const toggleDark = useToggle(isDark)
const { t } = useI18n()
</script>

<template>
  <main class="full pos-relative">
    <MainNavBar
      active-key="settings"
      :is-dark="isDark"
      @toggle-dark="toggleDark()"
    />

    <section class="pv-container pv-settings-body">
      <h1 class="pv-settings-title">{{ t('settings.pageTitle') }}</h1>
      <p class="pv-settings-intro">{{ t('settings.intro') }}</p>

      <AiSuggestSection />
    </section>
  </main>
</template>

<style scoped>
.pv-settings-body {
  max-width: var(--pv-page-width);
  padding-top: 32px;
  padding-bottom: 40px;
}
.pv-settings-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--el-text-color-primary, #303133);
  margin: 0 0 8px;
}
.pv-settings-intro {
  font-size: 14px;
  color: var(--el-text-color-secondary, #606266);
  margin: 0 0 20px;
  line-height: 1.6;
}
.pv-settings-card {
  margin-bottom: 16px;
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: var(--pv-card-radius);
  background: var(--el-bg-color, #fff);
}
</style>
