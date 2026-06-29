"""Preset catalog for the multi-provider LLM layer.

PaperVault suggests keywords via an LLM call. To keep the surface area
small while still supporting most major vendors, we ship a *catalog* of
named presets. Each preset records three things the dispatcher needs:

* ``protocol`` — ``"openai-compatible"`` or ``"anthropic"``. Wire format,
  not vendor. (StepFun's ``step_plan`` endpoint is Anthropic Messages
  compatible even though StepFun isn't Anthropic.)
* ``base_url`` — vendor endpoint root, no trailing slash. Override per
  request in Settings or the API payload.
* ``model`` — default model id. Users override per request from the UI.

The shape deliberately matches what the Vue frontend will mirror under
``src/constants/aiProviders.ts``: same keys, same fields, so the FE can
fetch the catalog from ``GET /api/v1/ai/providers`` and render the same
dropdown without duplicating strings on each side.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


PROTOCOL_OPENAI = "openai-compatible"
PROTOCOL_ANTHROPIC = "anthropic"


@dataclass(slots=True, frozen=True)
class ProviderPreset:
    key: str
    label: str
    protocol: str
    base_url: str
    model: str
    note: str
    env_key_var: str
    env_base_var: str
    env_model_var: str
    requires_max_tokens: bool = False

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


_PRESETS: Dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        key="openai",
        label="OpenAI",
        protocol=PROTOCOL_OPENAI,
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        note="OpenAI 官方 Chat Completions 接口。也可改成 gpt-5-mini 等。",
        env_key_var="OPENAI_API_KEY",
        env_base_var="OPENAI_API_BASE",
        env_model_var="PAPERVAULT_OPENAI_MODEL",
    ),
    "deepseek": ProviderPreset(
        key="deepseek",
        label="DeepSeek",
        protocol=PROTOCOL_OPENAI,
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        note="DeepSeek 官方 OpenAI-compatible 接口。",
        env_key_var="DEEPSEEK_API_KEY",
        env_base_var="PAPERVAULT_DEEPSEEK_BASE_URL",
        env_model_var="PAPERVAULT_DEEPSEEK_MODEL",
    ),
    "anthropic": ProviderPreset(
        key="anthropic",
        label="Anthropic Claude",
        protocol=PROTOCOL_ANTHROPIC,
        base_url="https://api.anthropic.com",
        model="claude-haiku-4-5",
        note="Anthropic Messages API。模型名按 Anthropic 控制台可用列表调整。",
        env_key_var="ANTHROPIC_API_KEY",
        env_base_var="ANTHROPIC_API_BASE",
        env_model_var="PAPERVAULT_ANTHROPIC_MODEL",
        requires_max_tokens=True,
    ),
    "qwen": ProviderPreset(
        key="qwen",
        label="通义千问 / DashScope",
        protocol=PROTOCOL_OPENAI,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
        note="阿里云百炼 OpenAI 兼容模式；国际站可改为 dashscope-intl 地址。",
        env_key_var="QWEN_API_KEY",
        env_base_var="QWEN_API_BASE",
        env_model_var="PAPERVAULT_QWEN_MODEL",
    ),
    "glm": ProviderPreset(
        key="glm",
        label="智谱 GLM",
        protocol=PROTOCOL_OPENAI,
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4-flash",
        note="智谱 BigModel OpenAI-compatible 接口。",
        env_key_var="GLM_API_KEY",
        env_base_var="GLM_API_BASE",
        env_model_var="PAPERVAULT_GLM_MODEL",
    ),
    "stepfun": ProviderPreset(
        key="stepfun",
        label="阶跃星辰 StepFun",
        protocol=PROTOCOL_ANTHROPIC,
        base_url="https://api.stepfun.com/step_plan/v1",
        model="step-3.7-flash",
        note="StepFun 的 step_plan 端点是 Anthropic Messages API 兼容（不是 OpenAI）。",
        env_key_var="STEPFUN_API_KEY",
        env_base_var="STEPFUN_BASE_URL",
        env_model_var="PAPERVAULT_STEPFUN_MODEL",
        requires_max_tokens=True,
    ),
    "custom": ProviderPreset(
        key="custom",
        label="自定义 / OpenAI 兼容",
        protocol=PROTOCOL_OPENAI,
        base_url="",
        model="",
        note="填写任何兼容 /chat/completions 或 /messages 的服务地址和模型名。",
        env_key_var="",
        env_base_var="",
        env_model_var="",
    ),
}


def get_preset(key: Optional[str]) -> ProviderPreset:
    """Return the preset for ``key``, falling back to ``custom``."""

    if key and key in _PRESETS:
        return _PRESETS[key]
    return _PRESETS["custom"]


def get_all_presets() -> List[ProviderPreset]:
    """Return presets in a stable order (custom last) for UI dropdowns."""

    ordered_keys = ["openai", "deepseek", "anthropic", "qwen", "glm", "stepfun", "custom"]
    return [_PRESETS[k] for k in ordered_keys]


def list_preset_keys() -> List[str]:
    return [p.key for p in get_all_presets()]
