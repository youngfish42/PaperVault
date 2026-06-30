/**
 * AI provider + keyword-suggestion endpoints (P2).
 *
 * Kept in a separate file from `api/paper.ts` so the legacy
 * `suggestKeywords(query: string)` signature in `paper.ts` keeps working
 * against the pre-P2 backend while we ship P2-B independently. The
 * settings-aware overload below is wired into the Settings page in P2-D.
 */

import request from '@/utils/axios'
import type { SuggestApiPayload } from '@/utils/aiSettings'
import type { AiProviderPreset, ProtocolKind } from '@/constants/aiProviders'

export interface SuggestRequestBody {
  query: string
  provider?: string
  base_url?: string
  model?: string
  api_key?: string
  protocol?: string
  temperature?: number
  max_keywords?: number
  max_tokens?: number
}

export interface SuggestApiResponse {
  keywords: string[]
  timecost_ms: number
  model: string
  provider: string
  protocol: string
}

export interface ProviderListResponse {
  items: AiProviderPreset[]
}

interface RawProviderPreset {
  key?: string
  label?: string
  protocol?: string
  base_url?: string
  model?: string
  note?: string
  env_key_var?: string
  env_base_var?: string
  env_model_var?: string
  requires_max_tokens?: boolean
}

interface RawProviderListResponse {
  items?: RawProviderPreset[]
}

const PROTOCOL_VALUES: readonly ProtocolKind[] = [
  'openai-compatible',
  'anthropic'
]

const normalizeProtocol = (value: unknown): ProtocolKind =>
  typeof value === 'string' && PROTOCOL_VALUES.includes(value as ProtocolKind)
    ? (value as ProtocolKind)
    : 'openai-compatible'

const normalizeProviderPreset = (raw: RawProviderPreset): AiProviderPreset => ({
  key: typeof raw.key === 'string' ? raw.key : '',
  label: typeof raw.label === 'string' ? raw.label : '',
  protocol: normalizeProtocol(raw.protocol),
  baseUrl: typeof raw.base_url === 'string' ? raw.base_url : '',
  model: typeof raw.model === 'string' ? raw.model : '',
  note: typeof raw.note === 'string' ? raw.note : '',
  envKeyVar: typeof raw.env_key_var === 'string' ? raw.env_key_var : '',
  envBaseVar: typeof raw.env_base_var === 'string' ? raw.env_base_var : '',
  envModelVar: typeof raw.env_model_var === 'string' ? raw.env_model_var : '',
  requiresMaxTokens: Boolean(raw.requires_max_tokens)
})

export const listAiProviders = async (): Promise<ProviderListResponse> => {
  const raw = await request<RawProviderListResponse>({
    url: '/v1/ai/providers',
    method: 'get',
    silent: true
  })
  const items = Array.isArray(raw?.items) ? raw.items : []
  return { items: items.map(normalizeProviderPreset) }
}

export const suggestKeywordsWithSettings = (
  query: string,
  payload: SuggestApiPayload = {}
) => {
  const body: SuggestRequestBody = { query, ...payload }
  return request<SuggestApiResponse>({
    url: '/v1/suggest',
    method: 'post',
    data: body,
    silent: true
  })
}
