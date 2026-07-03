<script setup lang="ts">
/**
 * Hero "AI search" dialog (P3).
 *
 * Two responsibilities:
 *   1. Take a free-form seed description from the user, ask the LLM for
 *      a handful of related keywords, let the user pick some, and emit the
 *      resulting OR-merged query string for the parent to push into the
 *      search box.
 *   2. Carry the "also rerank the result set by AI relevance" intent
 *      through to the parent. The actual rerank call is owned by the
 *      parent (HomeView) so the dialog stays free of search-flow state.
 *
 * The dialog reaches into the same provider / API key / settings that the
 * Settings page already configured (P2-D). If the user hasn't configured
 * anything yet, the request still goes out (the backend will resolve via
 * environment defaults and either succeed or 503 with a structured error
 * the dialog surfaces as a toast).
 *
 * ``MERGE_CAP`` (default 3) is a UX guard against the OR-merged query
 * ballooning past MAX_FETCH=5000. Each OR'd keyword multiplies the
 * candidate set; with a long tail of LLM-suggested keywords, even one
 * broad one (e.g. "offline reinforcement learning") is enough to
 * saturate the cap. Excess picks are silently truncated to the first
 * ``MERGE_CAP`` and a hint tells the user how many got dropped.
 */
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useI18n } from '@/utils/i18n'
import { suggestKeywordsWithSettings } from '@/api/ai'
import { loadAiSettings, loadApiKey, toApiPayload } from '@/utils/aiSettings'
import { buildOrMerge } from '@/utils/queryMerge'
import AiSuggestPanel from '@/components/AiSuggestPanel.vue'

const { t } = useI18n()
const router = useRouter()

const MERGE_CAP = 3

const props = defineProps<{
  visible: boolean
  defaultRerank?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'pick', payload: { query: string; rerank: boolean; seed: string }): void
}>()

const seed = ref('')
const keywords = ref<string[]>([])
const loading = ref(false)
const rerankEnabled = ref(props.defaultRerank ?? true)
const errorMsg = ref('')
const providerLabel = ref('')
// Sticky "the user has actually pressed Run at least once in this session"
// flag. Used by the template to distinguish three states that the raw
// `keywords.length + loading + errorMsg` triplet cannot express on its
// own: (a) untouched dialog, (b) loading, (c) run finished but returned
// an empty keyword list (200 + `keywords: []`). Without this flag the
// third state falls into a rendering hole where neither the suggest
// panel nor the error block is shown, so the user sees no feedback.
const hasRun = ref(false)

const visibleProxy = computed({
  get: () => props.visible,
  set: (v: boolean) => emit('update:visible', v)
})

watch(
  () => props.visible,
  open => {
    if (open) {
      // Reset transient state on each open so a stale result from the
      // previous session doesn't bleed into the new one. ``rerankEnabled``
      // is kept in sync with ``defaultRerank`` so the dialog remembers
      // the parent's last choice.
      keywords.value = []
      errorMsg.value = ''
      providerLabel.value = ''
      hasRun.value = false
      rerankEnabled.value = props.defaultRerank ?? rerankEnabled.value
      // Pre-fill the seed with the most recent pick if the caller passed
      // it (re-opening from a failed previous session). For the simple
      // hero entry path we just leave it blank.
    }
  }
)

const extractError = (err: unknown): string => {
  if (!err || typeof err !== 'object')
    return t('search.aiSearch.toastFailGeneric')
  const e = err as Record<string, any>
  return (
    e?.response?.data?.error?.message ||
    e?.response?.data?.message ||
    e?.response?.data?.msg ||
    e?.error?.message ||
    e?.data?.message ||
    e?.msg ||
    (e?.response?.status
      ? `[${e.response.status}] ${t('search.aiSearch.toastFailGeneric')}`
      : '') ||
    e?.message ||
    t('search.aiSearch.toastFailGeneric')
  )
}

const run = async (): Promise<void> => {
  const q = seed.value.trim()
  if (!q) {
    ElMessage.warning(t('search.aiSearch.toastNoSeed'))
    return
  }
  // Pre-flight: catch the most common AI failure up front so the user gets
  // an actionable hint instead of a backend 503. Both ``loadApiKey()`` and
  // ``loadAiSettings().provider`` come from this tab's session / local
  // storage — if *either* is empty here, the backend has nothing to work
  // with and would just raise ``LLM_NOT_CONFIGURED``. (apiKey lives in
  // sessionStorage and is wiped on tab close, so a user who saved a
  // provider yesterday still needs to re-paste their key today; the OR
  // makes sure we catch that half-configured state locally instead of
  // handing the user a raw 503.)
  if (!loadApiKey() || !loadAiSettings().provider) {
    errorMsg.value = t('search.aiSearch.toastNoKey')
    ElMessage.warning(t('search.aiSearch.toastNoKey'))
    hasRun.value = true
    return
  }
  loading.value = true
  errorMsg.value = ''
  keywords.value = []
  hasRun.value = true
  try {
    const payload = toApiPayload(loadAiSettings(), loadApiKey())
    const res = await suggestKeywordsWithSettings(q, payload)
    keywords.value = res.keywords || []
    if (res.provider && res.model) {
      providerLabel.value = `${res.provider} · ${res.model} · ${res.timecost_ms}ms`
    } else {
      providerLabel.value = ''
    }
  } catch (err) {
    errorMsg.value = extractError(err)
    ElMessage.error(t('search.aiSearch.toastFail', { msg: errorMsg.value }))
  } finally {
    loading.value = false
  }
}

const goSettings = (): void => {
  visibleProxy.value = false
  router.push('/settings')
}

const handlePickMany = (picked: string[]): void => {
  const baseSeed = seed.value.trim()
  const merged = buildOrMerge(baseSeed, picked, MERGE_CAP)
  emit('pick', { query: merged, rerank: rerankEnabled.value, seed: baseSeed })
  visibleProxy.value = false
}

const handleReplace = (kw: string): void => {
  emit('pick', { query: kw, rerank: rerankEnabled.value, seed: kw })
  visibleProxy.value = false
}
</script>

<template>
  <el-dialog
    v-model="visibleProxy"
    :title="t('search.aiSearch.dialogTitle')"
    width="520px"
    destroy-on-close
    :close-on-click-modal="false"
  >
    <div class="pv-ai-search-row">
      <el-input
        v-model="seed"
        :placeholder="t('search.aiSearch.seedPh')"
        clearable
        @keyup.enter="run"
      />
      <el-button type="primary" :loading="loading" @click="run">
        {{ t('search.aiSearch.run') }}
      </el-button>
    </div>

    <el-checkbox v-model="rerankEnabled" class="pv-ai-search-rerank">
      {{ t('search.aiSearch.rerank') }}
    </el-checkbox>

    <AiSuggestPanel
      v-if="loading || hasRun"
      :keywords="keywords"
      :loading="loading"
      :title="''"
      :provider-label="providerLabel"
      :empty-text="t('search.aiSearch.empty')"
      :single-replace-text="t('guess.replace')"
      :merge-button-text="t('guess.merge')"
      :merge-cap="MERGE_CAP"
      class="pv-ai-search-panel"
      @pick-many="handlePickMany"
      @replace="handleReplace"
    />
    <div v-if="errorMsg" class="pv-ai-search-error">
      {{ errorMsg }}
      <el-button
        v-if="!loadApiKey() || !loadAiSettings().provider"
        link
        type="primary"
        class="pv-ai-search-error-link"
        @click="goSettings"
      >
        {{ t('search.aiSearch.goSettings') }}
      </el-button>
    </div>
  </el-dialog>
</template>

<style scoped>
.pv-ai-search-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.pv-ai-search-row :deep(.el-input) {
  flex: 1 1 auto;
}
.pv-ai-search-rerank {
  margin-bottom: 12px;
}
.pv-ai-search-panel {
  margin-top: 4px;
}
.pv-ai-search-error {
  margin-top: 12px;
  font-size: 12px;
  color: var(--el-color-danger, #f56c6c);
}
.pv-ai-search-error-link {
  margin-left: 8px;
  font-size: 12px;
  padding: 0 4px;
}
</style>
