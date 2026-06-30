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
import type { AiProviderPreset } from '@/constants/aiProviders'

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

export const listAiProviders = () =>
  request<ProviderListResponse>({
    url: '/v1/ai/providers',
    method: 'get',
    silent: true
  })

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
