<script setup lang="ts">
/**
 * AI Suggest settings form (P2-D).
 *
 * Wires the P2-B infrastructure (`loadAiSettings` / `saveAiSettings` /
 * `loadApiKey` / `saveApiKey` / `toApiPayload` / `mergePresetDefaults`
 * and the `listAiProviders` / `suggestKeywordsWithSettings` API
 * wrappers) into a usable form. The form state is held in-memory and
 * only persisted on explicit Save — so a user can experiment without
 * trampling their saved config.
 *
 * The "Test it" panel below the form is intentionally a read-only
 * probe: it calls the current in-memory settings through
 * ``suggestKeywordsWithSettings`` and renders the returned keywords,
 * timing, and echoed model/provider. It never writes back to storage.
 */

import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getAiProvidersInOrder,
  type AiProviderPreset
} from '@/constants/aiProviders'
import { useI18n } from '@/utils/i18n'
import {
  clearAiSettings,
  clearApiKey,
  loadAiSettings,
  loadApiKey,
  mergePresetDefaults,
  saveAiSettings,
  saveApiKey,
  toApiPayload,
  type AiUserSettings
} from '@/utils/aiSettings'
import { listAiProviders, suggestKeywordsWithSettings } from '@/api/ai'
import type { SuggestApiResponse } from '@/api/ai'

const { t } = useI18n()

const form = reactive<AiUserSettings>({
  provider: '',
  baseUrl: '',
  model: '',
  protocol: '',
  temperature: null,
  maxKeywords: null,
  maxTokens: null
})

const apiKey = ref('')
const providers = ref<AiProviderPreset[]>(getAiProvidersInOrder())
const saved = ref(false)
const testing = ref(false)
const testQuery = ref('')
const testResult = ref<SuggestApiResponse | null>(null)
const testError = ref('')

const preset = computed<AiProviderPreset | null>(
  () => providers.value.find(p => p.key === form.provider) || null
)

onMounted(async () => {
  try {
    const res = await listAiProviders()
    if (res?.items?.length) providers.value = res.items
  } catch (err) {
    console.error('listAiProviders failed; using static catalog', err)
  }
  const loaded = loadAiSettings()
  Object.assign(form, loaded)
  apiKey.value = loadApiKey()
})

watch(
  () => form.provider,
  newKey => {
    if (!newKey) return
    const p = providers.value.find(x => x.key === newKey)
    if (!p) return
    Object.assign(form, mergePresetDefaults(form, p))
  }
)

let savedTimer: number | null = null

const flashSaved = (): void => {
  saved.value = true
  if (savedTimer !== null) {
    clearTimeout(savedTimer)
  }
  savedTimer = window.setTimeout(() => {
    saved.value = false
    savedTimer = null
  }, 2000)
}

onBeforeUnmount(() => {
  if (savedTimer !== null) {
    clearTimeout(savedTimer)
    savedTimer = null
  }
})

const handleSave = (): void => {
  saveAiSettings(form)
  if (apiKey.value) saveApiKey(apiKey.value)
  else clearApiKey()
  flashSaved()
}

const handleClear = (): void => {
  clearAiSettings()
  clearApiKey()
  Object.assign(form, {
    provider: '',
    baseUrl: '',
    model: '',
    protocol: '',
    temperature: null,
    maxKeywords: null,
    maxTokens: null
  })
  apiKey.value = ''
  testResult.value = null
  testError.value = ''
  testQuery.value = ''
}

const extractErrorMessage = (err: unknown): string => {
  if (!err || typeof err !== 'object')
    return t('settings.aiSuggest.test.errorUnknown')
  const e = err as Record<string, any>
  return (
    e?.error?.message ||
    e?.data?.message ||
    e?.msg ||
    e?.message ||
    t('settings.aiSuggest.test.errorUnknown')
  )
}

const handleTest = async (): Promise<void> => {
  const q = testQuery.value.trim()
  if (!q) {
    ElMessage.warning(t('settings.aiSuggest.test.errorQuery'))
    return
  }
  if (!form.provider) {
    ElMessage.warning(t('settings.aiSuggest.test.errorProvider'))
    return
  }
  testing.value = true
  testError.value = ''
  testResult.value = null
  try {
    const payload = toApiPayload(form, apiKey.value)
    const res = await suggestKeywordsWithSettings(q, payload)
    testResult.value = res
  } catch (err) {
    const msg = extractErrorMessage(err)
    testError.value = msg
    ElMessage.error(msg)
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <el-card shadow="never" class="pv-settings-card">
    <template #header>
      <div class="pv-ai-header">
        <span class="pv-settings-card-title">
          {{ t('settings.aiSuggest.title') }}
        </span>
        <span v-if="saved" class="pv-ai-saved">
          {{ t('settings.aiSuggest.saved') }}
        </span>
      </div>
    </template>

    <el-form :label-position="'top'" class="pv-ai-form">
      <el-form-item :label="t('settings.aiSuggest.form.provider')">
        <el-select v-model="form.provider" filterable style="width: 100%">
          <el-option
            v-for="p in providers"
            :key="p.key"
            :label="`${p.label} (${p.protocol})`"
            :value="p.key"
          />
        </el-select>
        <div v-if="preset?.note" class="pv-ai-hint">{{ preset.note }}</div>
      </el-form-item>

      <el-row :gutter="12">
        <el-col :span="14">
          <el-form-item :label="t('settings.aiSuggest.form.baseUrl')">
            <el-input
              v-model="form.baseUrl"
              :placeholder="preset?.baseUrl || ''"
            />
          </el-form-item>
        </el-col>
        <el-col :span="10">
          <el-form-item :label="t('settings.aiSuggest.form.model')">
            <el-input v-model="form.model" :placeholder="preset?.model || ''" />
          </el-form-item>
        </el-col>
      </el-row>

      <el-form-item :label="t('settings.aiSuggest.form.apiKey')">
        <el-input
          v-model="apiKey"
          type="password"
          show-password
          :placeholder="t('settings.aiSuggest.form.apiKeyPh')"
          autocomplete="off"
        />
        <div class="pv-ai-hint">{{ t('settings.aiSuggest.hint.apiKey') }}</div>
      </el-form-item>

      <el-form-item :label="t('settings.aiSuggest.form.protocol')">
        <el-input :model-value="form.protocol" readonly />
      </el-form-item>

      <el-row :gutter="12">
        <el-col :span="8">
          <el-form-item :label="t('settings.aiSuggest.form.temperature')">
            <el-input-number
              v-model="form.temperature"
              :min="0"
              :max="2"
              :step="0.1"
              :precision="1"
              controls-position="right"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
        <el-col :span="8">
          <el-form-item :label="t('settings.aiSuggest.form.maxKeywords')">
            <el-input-number
              v-model="form.maxKeywords"
              :min="1"
              :max="50"
              :step="1"
              controls-position="right"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
        <el-col v-if="preset?.requiresMaxTokens" :span="8">
          <el-form-item :label="t('settings.aiSuggest.form.maxTokens')">
            <el-input-number
              v-model="form.maxTokens"
              :min="1"
              :max="4096"
              :step="64"
              controls-position="right"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
      </el-row>

      <div class="pv-ai-actions">
        <el-button @click="handleClear">
          {{ t('settings.aiSuggest.action.clear') }}
        </el-button>
        <el-button type="primary" @click="handleSave">
          {{ t('settings.aiSuggest.action.save') }}
        </el-button>
      </div>
    </el-form>

    <el-divider />

    <h3 class="pv-ai-test-title">
      {{ t('settings.aiSuggest.test.title') }}
    </h3>
    <div class="pv-ai-test-row">
      <el-input
        v-model="testQuery"
        :placeholder="t('settings.aiSuggest.test.queryPh')"
        clearable
        @keyup.enter="handleTest"
      />
      <el-button
        type="primary"
        :loading="testing"
        :disabled="!form.provider"
        @click="handleTest"
      >
        {{ t('settings.aiSuggest.test.button') }}
      </el-button>
    </div>

    <div v-if="testResult" class="pv-ai-test-result">
      <div class="pv-ai-test-meta">
        <span>{{ testResult.provider }} / {{ testResult.model }}</span>
        <span class="pv-ai-test-time"> {{ testResult.timecost_ms }} ms </span>
      </div>
      <div v-if="testResult.keywords.length" class="pv-ai-test-chips">
        <el-tag
          v-for="kw in testResult.keywords"
          :key="kw"
          type="primary"
          effect="plain"
          size="default"
        >
          {{ kw }}
        </el-tag>
      </div>
      <el-empty
        v-else
        :description="t('settings.aiSuggest.test.empty')"
        :image-size="60"
      />
    </div>
    <el-alert
      v-else-if="testError"
      class="pv-ai-test-error"
      type="error"
      :title="testError"
      :closable="false"
      show-icon
    />
  </el-card>
</template>

<style scoped>
.pv-ai-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.pv-ai-saved {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-color-success, #67c23a);
}
.pv-ai-form :deep(.el-form-item) {
  margin-bottom: 14px;
}
.pv-ai-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  line-height: 1.5;
  margin-top: 4px;
}
.pv-ai-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}
.pv-ai-test-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary, #303133);
  margin: 0 0 8px;
}
.pv-ai-test-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.pv-ai-test-row :deep(.el-input) {
  flex: 1 1 auto;
}
.pv-ai-test-result {
  margin-top: 14px;
  padding: 12px 14px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  border-left: 3px solid var(--el-color-primary, #6f5ed3);
  border-radius: 4px;
}
.pv-ai-test-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--el-text-color-secondary, #606266);
  margin-bottom: 8px;
}
.pv-ai-test-time {
  font-variant-numeric: tabular-nums;
}
.pv-ai-test-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.pv-ai-test-error {
  margin-top: 14px;
}
</style>
