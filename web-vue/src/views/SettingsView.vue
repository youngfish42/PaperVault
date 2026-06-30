<script setup lang="ts">
import { useDark, useToggle } from '@vueuse/core'
import MainNavBar from '@/components/MainNavBar.vue'
import { useI18n } from '@/utils/i18n'

/**
 * P2-C Settings page shell. No data fetching, no forms — those arrive
 * in P2-D when the AI Suggest section starts consuming P2-B's
 * ``listAiProviders()`` and ``suggestKeywordsWithSettings()``.
 *
 * The visual structure (title + intro + two ``el-card`` placeholders)
 * mirrors AdvancedSearchView's density so the page doesn't feel empty.
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

      <el-card shadow="never" class="pv-settings-card">
        <template #header>
          <span class="pv-settings-card-title">
            {{ t('settings.aiSuggest.title') }}
          </span>
        </template>
        <el-alert
          type="info"
          :title="t('settings.wip')"
          :description="t('settings.aiSuggest.desc')"
          show-icon
          :closable="false"
        />
      </el-card>

      <el-card shadow="never" class="pv-settings-card">
        <template #header>
          <span class="pv-settings-card-title">
            {{ t('settings.about.title') }}
          </span>
        </template>
        <p class="pv-settings-about-body">{{ t('settings.about.body') }}</p>
      </el-card>
    </section>
  </main>
</template>

<style scoped>
.pv-settings-body {
  max-width: 720px;
  padding-top: 28px;
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
  border-radius: 10px;
  background: var(--el-bg-color, #fff);
}
.pv-settings-card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary, #303133);
}
.pv-settings-about-body {
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--el-text-color-regular, #4c4d4f);
  margin: 0;
}
</style>
