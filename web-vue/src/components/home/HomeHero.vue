<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import AiSearchDialog from '@/components/AiSearchDialog.vue'
import { useI18n } from '@/utils/i18n'

const props = defineProps<{ query: string }>()
const emit = defineEmits<{
  'update:query': [v: string]
  search: []
  'ai-pick': [payload: { query: string; rerank: boolean; seed: string }]
}>()

const { t } = useI18n()
const router = useRouter()

const cheatsheetOpen = ref(false)
const aiDialogOpen = ref(false)
const aiSearch = ref<InstanceType<typeof AiSearchDialog> | null>(null)
const aiLoading = computed(() => aiSearch.value?.loading ?? false)
const submitSearch = (): void => {
  if (aiDialogOpen.value) {
    void aiSearch.value?.run()
  } else {
    emit('search')
  }
}

const queryModel = computed({
  get: () => props.query,
  set: (v: string) => emit('update:query', v)
})
</script>

<template>
  <section class="pv-hero" :class="{ 'is-ai': aiDialogOpen }">
    <div class="pv-container pv-hero-inner">
      <h1 class="pv-hero-title">
        <a href="/"><span>Paper</span><b>Vault</b></a>
      </h1>
      <p class="pv-hero-slogan">{{ t('app.slogan') }}</p>

      <div class="pv-search-modes" role="group" :aria-label="t('search.mode.label')">
        <button type="button" :aria-pressed="!aiDialogOpen" :disabled="aiLoading"
          @click="aiDialogOpen = false">{{ t('search.mode.standard') }}</button>
        <button type="button" :aria-pressed="aiDialogOpen" :disabled="aiLoading"
          @click="aiDialogOpen = true">{{ t('search.aiSearch.button') }}</button>
        <button type="button" :aria-pressed="false" :disabled="aiLoading"
          @click="router.push('/advanced')">{{ t('search.tab.advanced') }}</button>
      </div>
      <div class="pv-hero-searchbox">
        <input
          v-model="queryModel"
          class="pv-hero-searchbox-input"
          :placeholder="aiDialogOpen ? t('search.aiSearch.seedPh') : t('search.placeholder.short')"
          :aria-label="aiDialogOpen ? t('search.aiSearch.button') : t('search.button')"
          :disabled="aiLoading"
          @keydown.enter="!$event.isComposing && submitSearch()"
        />
        <button
          type="button"
          class="pv-hero-searchbox-btn"
          :title="aiDialogOpen ? t('search.aiSearch.run') : t('search.button')"
          :aria-label="aiDialogOpen ? t('search.aiSearch.run') : t('search.button')"
          :disabled="aiLoading"
          :aria-busy="aiLoading"
          @click="submitSearch"
        >
          <span v-if="aiLoading">…</span>
          <el-icon><Search /></el-icon>
        </button>
      </div>

      <div class="pv-ai-slot">
        <AiSearchDialog
          ref="aiSearch"
          v-model:visible="aiDialogOpen"
          :seed-value="queryModel"
          @pick="$emit('ai-pick', $event)"
        />
      </div>

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
    <footer class="pv-hero-footer"><span>© 2026 PaperVault</span><span class="pv-hero-footer-spacer" /><a href="https://github.com/youngfish42/PaperVault" target="_blank" rel="noopener noreferrer">GitHub ↗</a></footer>
  </section>
</template>

<style scoped>
.pv-hero {
  --pv-search-width: 800px;
  width: 100%;
  min-height: calc(100vh - 58px);
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
  width: 100%;
  max-width: var(--pv-page-width);
  flex: 1 1 auto;
  box-sizing: border-box;
  padding-top: 28px;
  padding-bottom: 28px;
  text-align: center;
  display: flex;
  flex-direction: column;
  justify-content: center;
  /* Keep the focal search group slightly above the viewport midpoint. */
  transform: translateY(-88px);
}
.pv-hero-title {
  margin: 0 0 6px;
  font-size: 46px;
  letter-spacing: -0.055em;
  font-weight: 750;
  user-select: none;
}
.pv-hero-title a {
  text-decoration: none;
  color: var(--el-text-color-primary, #303133);
  letter-spacing: inherit;
}
.pv-hero-title a:hover {
  text-decoration: underline;
}
.pv-hero-slogan {
  margin: 0 0 32px;
  font-size: 16px;
  font-weight: 500;
  color: var(--el-text-color-regular, #606266);
  user-select: none;
}
.pv-hero-searchbox {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  margin: 0 auto;
  width: 100%;
  max-width: var(--pv-search-width);
  height: 68px;
  padding: 0 6px 0 24px;
  background: var(--pv-page-bg);
  border: 1.5px solid var(--el-color-primary-light-5, #b3d8ff);
  border-radius: 14px;
  box-shadow: 0 6px 22px rgba(38, 50, 80, 0.08);
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
  letter-spacing: inherit;
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
  width: 46px;
  height: 46px;
  border-radius: 11px;
  border: none;
  background: var(--el-color-primary, #6f5ed3);
  color: #fff;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  transition: background 0.18s ease, transform 0.18s ease;
}
.pv-hero-searchbox-btn:hover {
  background: var(--el-color-primary-dark-2, #5847c0);
  transform: scale(1.04);
}
/* The mode picker and input share exactly the same outer edges. */
.pv-ai-slot { width: 100%; max-width: var(--pv-search-width); min-height: 118px; margin: 0 auto; }
.pv-search-modes {
  display: flex;
  justify-content: center;
  gap: 6px;
  width: 100%;
  max-width: var(--pv-search-width);
  min-height: 40px;
  margin: 0 auto 14px;
}
.pv-search-modes button {
  flex: 0 0 116px;
  height: 40px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--el-text-color-regular);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
  transition: background .16s ease, color .16s ease;
}
.pv-search-modes button[aria-pressed='true'] {
  background: var(--el-fill-color);
  border-color: var(--el-border-color);
  color: var(--el-text-color-primary);
  font-weight: 600;
}
.pv-search-modes button:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
}
.pv-search-modes button:disabled,
.pv-hero-searchbox-btn:disabled { cursor: wait; opacity: 0.65; }
.pv-hero-hint {
  margin: 12px 0 22px;
  font-size: 13px;
  color: var(--el-text-color-regular, #606266);
}
.pv-hero:not(.is-ai) .pv-hero-hint { margin-top: -54px; }
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
  max-width: var(--pv-search-width);
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
  .pv-hero-inner { transform: translateY(-52px); }
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
.pv-hero-footer{display:flex;align-items:center;gap:18px;width:100%;padding:16px max(24px,calc((100vw - var(--pv-page-width)) / 2));box-sizing:border-box;border-top:1px solid var(--el-border-color-lighter,#ebeef5);color:var(--el-text-color-secondary,#909399);font-size:11px;letter-spacing:.02em}.pv-hero-footer-spacer{flex:1}.pv-hero-footer a{color:inherit;text-decoration:none}.pv-hero-footer a:hover{color:var(--el-color-primary,#409eff)}@media(max-width:600px){.pv-hero-footer{gap:10px;font-size:10px}.pv-hero-footer span:last-child{display:none}}
</style>
