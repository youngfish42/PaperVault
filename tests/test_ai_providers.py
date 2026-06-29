"""Pure-data tests for the provider preset catalog.

These tests don't touch the network or any SDK; they pin the catalog
shape so the FE mirror under ``web-vue/src/constants/aiProviders.ts``
cannot drift from the backend silently.
"""

from __future__ import annotations

from papervault.services.ai_providers import (
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
    get_all_presets,
    get_preset,
    list_preset_keys,
)


def test_all_presets_include_stepfun_as_anthropic():
    presets = {p.key: p for p in get_all_presets()}
    assert "stepfun" in presets
    assert presets["stepfun"].protocol == PROTOCOL_ANTHROPIC
    assert presets["stepfun"].base_url == "https://api.stepfun.com/step_plan/v1"


def test_stepfun_differs_from_plan_pilot_stepfun():
    # Plan-pilot ships an OpenAI-compatible StepFun preset for the
    # ``api.stepfun.ai`` chat endpoint. PaperVault ships StepFun's
    # ``step_plan`` endpoint which is Anthropic Messages compatible.
    presets = {p.key: p for p in get_all_presets()}
    assert presets["stepfun"].protocol != PROTOCOL_OPENAI


def test_get_preset_unknown_falls_back_to_custom():
    p = get_preset("does-not-exist")
    assert p.key == "custom"
    assert p.base_url == ""


def test_list_preset_keys_is_stable_order():
    keys = list_preset_keys()
    assert keys[0] == "openai"
    assert keys[-1] == "custom"
    assert "anthropic" in keys
    assert "stepfun" in keys


def test_presets_carry_env_var_names():
    presets = {p.key: p for p in get_all_presets()}
    assert presets["openai"].env_key_var == "OPENAI_API_KEY"
    assert presets["deepseek"].env_key_var == "DEEPSEEK_API_KEY"
    assert presets["anthropic"].env_key_var == "ANTHROPIC_API_KEY"
    assert presets["qwen"].env_key_var == "QWEN_API_KEY"
    assert presets["glm"].env_key_var == "GLM_API_KEY"
    assert presets["stepfun"].env_key_var == "STEPFUN_API_KEY"
    assert presets["stepfun"].requires_max_tokens is True
    assert presets["openai"].requires_max_tokens is False


def test_as_dict_is_jsonable_for_dropdown_rendering():
    import json

    for preset in get_all_presets():
        json.dumps(preset.as_dict())
