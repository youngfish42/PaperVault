<script setup lang="ts">
import { computed, ref } from 'vue'
import AiSearchDialog from '@/components/AiSearchDialog.vue'
import { useI18n } from '@/utils/i18n'

const props = defineProps<{ query: string }>()
const emit = defineEmits<{
  'update:query': [v: string]
  search: []
  'ai-pick': [payload: { query: string; rerank: boolean; seed: string }]
}>()

const { t } = useI18n()

const cheatsheetOpen = ref(false)
const aiDialogOpen = ref(false)

const queryModel = computed({
  get: () => props.query,
  set: (v: string) => emit('update:query', v)
})
</script>

<template>
  <section class="pv-hero">
    <div class="pv-container pv-hero-inner">
      <h1 class="pv-hero-title">
        <a href="/">{{ t('app.title') }}</a>
      </h1>
      <p class="pv-hero-slogan">{{ t('app.slogan') }}</p>

      <div class="pv-hero-searchbox">
        <input
          v-model="queryModel"
          class="pv-hero-searchbox-input"
          :placeholder="t('search.placeholder.short')"
          @keyup.enter="$emit('search')"
        />
        <button
          type="button"
          class="pv-hero-searchbox-btn"
          :title="t('search.button')"
          @click="$emit('search')"
        >
          <el-icon><Search /></el-icon>
        </button>
      </div>

      <div class="pv-hero-ai-row">
        <el-button class="pv-hero-ai-btn" plain @click="aiDialogOpen = true">
          <el-icon><MagicStick /></el-icon>
          <span>{{ t('search.aiSearch.button') }}</span>
        </el-button>
        <span class="pv-hero-ai-hint">{{ t('search.aiSearch.hint') }}</span>
      </div>
      <AiSearchDialog
        v-model:visible="aiDialogOpen"
        @pick="$emit('ai-pick', $event)"
      />

      <p class="pv-hero-hint">
        {{ t('search.heroHint')
        }}<router-link to="/advanced" class="pv-hero-hint-link">{{
          t('search.heroHintLink')
        }}</router-link
        >{{ t('search.heroHintTail') }}
      </p>

      <div class="pv-hero-syntax">
        <button
          type="button"
          class="pv-hero-syntax-toggle"
          :aria-expanded="cheatsheetOpen"
          @click="cheatsheetOpen = !cheatsheetOpen"
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
            class="pv-hero-syntax-panel pv-syntax-scope"
            v-html="t('search.dslTipHtml')"
          />
        </transition>
      </div>
    </div>
  </section>
</template>

<style scoped>
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
.pv-fade-enter-active,
.pv-fade-leave-active {
  transition: opacity 0.2s ease;
}
.pv-fade-enter-from,
.pv-fade-leave-to {
  opacity: 0;
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
}
</style>
