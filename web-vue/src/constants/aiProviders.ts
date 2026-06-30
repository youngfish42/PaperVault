/**
 * Frontend mirror of `papervault.services.ai_providers.ProviderPreset`.
 *
 * Catalog values are kept in lockstep with the backend by hand. Field
 * *names* are NOT: this module uses camelCase (`baseUrl`, `envKeyVar`,
 * `requiresMaxTokens`) for ergonomic FE consumption, while the backend's
 * `ProviderPreset.as_dict()` emits snake_case (`base_url`, `env_key_var`,
 * `requires_max_tokens`) directly from `dataclasses.asdict`. The two
 * shapes are bridged at ingress by `normalizeProviderPreset` in
 * `@/api/ai.ts`, which maps the wire payload of `GET /api/v1/ai/providers`
 * into this `AiProviderPreset` interface. The Settings page (P2-C) should
 * always consume the normalized version, never cast the raw response.
 *
 * `StepFun`'s `step_plan` endpoint speaks the Anthropic Messages protocol,
 * not the OpenAI-compatible chat completions used by plan-pilot's StepFun
 * preset (`api.stepfun.ai`). The P2-A connectivity check confirmed that
 * `https://api.stepfun.com/step_plan/v1/messages` returns the expected
 * 401 with a dummy `x-api-key`, matching the Anthropic SDK's wire format.
 */

export type ProtocolKind = 'openai-compatible' | 'anthropic'

export interface AiProviderPreset {
  key: string
  label: string
  protocol: ProtocolKind
  baseUrl: string
  model: string
  note: string
  envKeyVar: string
  envBaseVar: string
  envModelVar: string
  requiresMaxTokens: boolean
}

export const AI_PROVIDER_PRESETS: Record<string, AiProviderPreset> = {
  openai: {
    key: 'openai',
    label: 'OpenAI',
    protocol: 'openai-compatible',
    baseUrl: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    note: 'OpenAI 官方 Chat Completions 接口。也可改成 gpt-5-mini 等更轻模型。',
    envKeyVar: 'OPENAI_API_KEY',
    envBaseVar: 'OPENAI_API_BASE',
    envModelVar: 'PAPERVAULT_OPENAI_MODEL',
    requiresMaxTokens: false
  },
  deepseek: {
    key: 'deepseek',
    label: 'DeepSeek',
    protocol: 'openai-compatible',
    baseUrl: 'https://api.deepseek.com',
    model: 'deepseek-chat',
    note: 'DeepSeek 官方 OpenAI-compatible 接口。',
    envKeyVar: 'DEEPSEEK_API_KEY',
    envBaseVar: 'PAPERVAULT_DEEPSEEK_BASE_URL',
    envModelVar: 'PAPERVAULT_DEEPSEEK_MODEL',
    requiresMaxTokens: false
  },
  anthropic: {
    key: 'anthropic',
    label: 'Anthropic Claude',
    protocol: 'anthropic',
    baseUrl: 'https://api.anthropic.com',
    model: 'claude-haiku-4-5',
    note: 'Anthropic Messages API。模型名按 Anthropic 控制台可用列表调整。',
    envKeyVar: 'ANTHROPIC_API_KEY',
    envBaseVar: 'ANTHROPIC_API_BASE',
    envModelVar: 'PAPERVAULT_ANTHROPIC_MODEL',
    requiresMaxTokens: true
  },
  qwen: {
    key: 'qwen',
    label: '通义千问 / DashScope',
    protocol: 'openai-compatible',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-plus',
    note: '阿里云百炼 OpenAI 兼容模式；国际站可改为 dashscope-intl 地址。',
    envKeyVar: 'QWEN_API_KEY',
    envBaseVar: 'QWEN_API_BASE',
    envModelVar: 'PAPERVAULT_QWEN_MODEL',
    requiresMaxTokens: false
  },
  glm: {
    key: 'glm',
    label: '智谱 GLM',
    protocol: 'openai-compatible',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4-flash',
    note: '智谱 BigModel OpenAI-compatible 接口。',
    envKeyVar: 'GLM_API_KEY',
    envBaseVar: 'GLM_API_BASE',
    envModelVar: 'PAPERVAULT_GLM_MODEL',
    requiresMaxTokens: false
  },
  stepfun: {
    key: 'stepfun',
    label: '阶跃星辰 StepFun',
    protocol: 'anthropic',
    baseUrl: 'https://api.stepfun.com/step_plan/v1',
    model: 'step-3.7-flash',
    note: 'StepFun 的 step_plan 端点是 Anthropic Messages API 兼容（不是 OpenAI）。',
    envKeyVar: 'STEPFUN_API_KEY',
    envBaseVar: 'STEPFUN_BASE_URL',
    envModelVar: 'PAPERVAULT_STEPFUN_MODEL',
    requiresMaxTokens: true
  },
  custom: {
    key: 'custom',
    label: '自定义 / OpenAI 兼容',
    protocol: 'openai-compatible',
    baseUrl: '',
    model: '',
    note: '填写任何兼容 /chat/completions 或 /messages 的服务地址和模型名。',
    envKeyVar: '',
    envBaseVar: '',
    envModelVar: '',
    requiresMaxTokens: false
  }
}

export function getAiProviderPreset(
  key: string | null | undefined
): AiProviderPreset {
  if (key && AI_PROVIDER_PRESETS[key]) return AI_PROVIDER_PRESETS[key]
  return AI_PROVIDER_PRESETS.custom
}

export function getAiProvidersInOrder(): AiProviderPreset[] {
  return [
    AI_PROVIDER_PRESETS.openai,
    AI_PROVIDER_PRESETS.deepseek,
    AI_PROVIDER_PRESETS.anthropic,
    AI_PROVIDER_PRESETS.qwen,
    AI_PROVIDER_PRESETS.glm,
    AI_PROVIDER_PRESETS.stepfun,
    AI_PROVIDER_PRESETS.custom
  ]
}
