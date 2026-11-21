/**
 * localStorage + sessionStorage adapter for AI provider settings.
 *
 * Storage split:
 *   - Non-secret knobs (provider, baseUrl, model, protocol, temperature,
 *     maxKeywords, maxTokens) live in localStorage so the user's last pick
 *     survives a browser restart.
 *   - The API key lives in sessionStorage: it is wiped when the tab closes,
 *     which is a reasonable safety floor for a public-domain paper search
 *     site without forcing the user to re-paste a key every refresh.
 *
 * `provider = ''` is the explicit "follow server default" sentinel. The
 * backend (`papervault.services.suggest._resolve_provider`) treats an
 * absent provider the same way, so the round-trip is lossless.
 */

import type { AiProviderPreset } from '@/constants/aiProviders'

const SETTINGS_KEY = 'papervault.ai.settings.v1'
const API_KEY_KEY = 'papervault.ai.apiKey.v1'

export interface AiUserSettings {
  provider: string
  baseUrl: string
  model: string
  protocol: string
  temperature: number | null
  maxKeywords: number | null
  maxTokens: number | null
}

const EMPTY_SETTINGS: AiUserSettings = {
  provider: '',
  baseUrl: '',
  model: '',
  protocol: '',
  temperature: null,
  maxKeywords: null,
  maxTokens: null
}

function safeGet(storage: Storage, key: string): string | null {
  try {
    return storage.getItem(key)
  } catch {
    return null
  }
}

function safeSet(storage: Storage, key: string, value: string): void {
  try {
    storage.setItem(key, value)
  } catch {
    // ignore quota / private-mode errors; settings are best-effort
  }
}

function safeRemove(storage: Storage, key: string): void {
  try {
    storage.removeItem(key)
  } catch {
    // ignore
  }
}

function pickString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function pickFiniteNumber(
  value: unknown,
  min: number,
  max: number
): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  if (value < min || value > max) return null
  return value
}

function sanitizeAiSettings(raw: Record<string, unknown>): AiUserSettings {
  return {
    provider: pickString(raw.provider),
    baseUrl: pickString(raw.baseUrl),
    model: pickString(raw.model),
    protocol: pickString(raw.protocol),
    temperature: pickFiniteNumber(raw.temperature, 0, 2),
    maxKeywords: pickFiniteNumber(raw.maxKeywords, 1, 50),
    maxTokens: pickFiniteNumber(raw.maxTokens, 1, 4096)
  }
}

export function loadAiSettings(): AiUserSettings {
  const raw = safeGet(
    typeof localStorage !== 'undefined'
      ? localStorage
      : (null as unknown as Storage),
    SETTINGS_KEY
  )
  if (!raw) return { ...EMPTY_SETTINGS }
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return { ...EMPTY_SETTINGS }
    return sanitizeAiSettings(parsed as Record<string, unknown>)
  } catch {
    return { ...EMPTY_SETTINGS }
  }
}

export function saveAiSettings(settings: AiUserSettings): void {
  if (typeof localStorage === 'undefined') return
  safeSet(localStorage, SETTINGS_KEY, JSON.stringify(settings))
}

export function clearAiSettings(): void {
  if (typeof localStorage !== 'undefined')
    safeRemove(localStorage, SETTINGS_KEY)
}

export function loadApiKey(): string {
  if (typeof sessionStorage === 'undefined') return ''
  return safeGet(sessionStorage, API_KEY_KEY) || ''
}

export function saveApiKey(key: string): void {
  if (typeof sessionStorage === 'undefined') return
  if (key) safeSet(sessionStorage, API_KEY_KEY, key)
  else safeRemove(sessionStorage, API_KEY_KEY)
}

export function clearApiKey(): void {
  if (typeof sessionStorage !== 'undefined')
    safeRemove(sessionStorage, API_KEY_KEY)
}

export interface SuggestApiPayload {
  provider?: string
  base_url?: string
  model?: string
  api_key?: string
  protocol?: string
  temperature?: number
  max_keywords?: number
  max_tokens?: number
}

/**
 * Project the user's UI settings into the wire payload accepted by
 * `POST /api/v1/suggest`. Empty / unset fields are dropped so the backend
 * can keep falling back to its own settings / env defaults.
 */
export function toApiPayload(
  settings: AiUserSettings,
  apiKey: string
): SuggestApiPayload {
  const payload: SuggestApiPayload = {}
  if (settings.provider) payload.provider = settings.provider
  if (settings.baseUrl) payload.base_url = settings.baseUrl
  if (settings.model) payload.model = settings.model
  if (apiKey) payload.api_key = apiKey
  if (settings.protocol) payload.protocol = settings.protocol
  if (settings.temperature != null) payload.temperature = settings.temperature
  if (settings.maxKeywords != null) payload.max_keywords = settings.maxKeywords
  if (settings.maxTokens != null) payload.max_tokens = settings.maxTokens
  return payload
}

/**
 * Re-fill empty user fields with the matching preset defaults so the UI
 * does not have to second-guess the catalog. Used when the user picks a
 * different provider in the dropdown.
 */
export function mergePresetDefaults(
  settings: AiUserSettings,
  preset: AiProviderPreset
): AiUserSettings {
  return {
    provider: preset.key,
    baseUrl: settings.baseUrl || preset.baseUrl,
    model: settings.model || preset.model,
    protocol: settings.protocol || preset.protocol,
    temperature: settings.temperature,
    maxKeywords: settings.maxKeywords,
    maxTokens: settings.maxTokens
  }
}
