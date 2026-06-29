# PaperVault Changelog

## P2-A · 多 LLM 提供商：后端预设与协议分发（2026-06-29）

将原本只支持 OpenAI / DeepSeek 的「猜你想搜」服务升级为可插拔的多提供商架构，本轮只动后端、前端下一轮再做。

### 新增

- **`papervault/services/ai_providers.py`**：预设目录。内置 7 个预设：
  - `openai`（OpenAI 官方 Chat Completions）
  - `deepseek`（DeepSeek OpenAI-compatible）
  - `anthropic`（Anthropic Messages API）
  - `qwen` / `glm`（通义千问 / 智谱 BigModel OpenAI-compatible）
  - `stepfun`（**StepFun 的 `step_plan` 端点**，Anthropic Messages API 兼容，不是 OpenAI）
  - `custom`（用户自填 OpenAI-compatible 接口地址与模型）
- **`papervault/services/ai_clients.py`**：协议级 SDK 调度封装。
  - `call_openai_compatible()` 走 `openai` SDK。
  - `call_anthropic()` 走 `anthropic` SDK（首次为项目引入，附带 `requirements.txt` 里追加 `anthropic>=0.40,<1`）。
- **`GET /api/v1/ai/providers`**：前端下拉框数据源，字段与 `ProviderPreset.as_dict()` 一一对应，方便后续 P2-B 同步落地。

### 修改

- **`papervault/services/suggest.py`** 重写为 dispatch 模型：
  - `_resolve_provider()` 按 `req → settings → env` 三层优先级解析协议 / base_url / model / api_key。
  - 协议字段（`openai-compatible` / `anthropic`）由预设决定，调用方也可用 `protocol` 覆盖。
  - 自定义 provider 缺 `base_url` 或 `model` 时返回 400 BAD_REQUEST。
  - 保留旧版 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` 兜底，确保历史部署升级不破坏。
- **`papervault/schemas.py`**：`SuggestRequest` 新增 `provider / base_url / model / api_key / protocol / temperature / max_tokens / max_keywords`，老字段保持向后兼容；`SuggestResponse` 新增 `provider / protocol`，前端无需再盲猜。
- **`papervault/api/v1/suggest.py`** 透传新字段并把请求包成 `SuggestionRequest` 后再下发。
- **`papervault/config.py`**：`suggest_provider` 默认值由 `deepseek` 改为 `""`（避免新部署被默认值锁死），新增 `anthropic_model` / `anthropic_max_tokens`。

### 兼容性

- 老的 `POST /api/v1/suggest { query, model, max_keywords }` 三字段请求仍然有效：缺省走旧版 DeepSeek / OpenAI 兜底。
- 老的 `PAPERVAULT_SUGGEST_PROVIDER=deepseek` 设置不会被默认值覆盖，照旧。

### 测试

- `tests/test_ai_providers.py`（6 项）：目录形状契约，含 StepFun ≠ plan-pilot 那个 OpenAI-compatible StepFun 的对照。
- `tests/test_ai_clients.py`（5 项）：缺 key 时返回 `LLM_NOT_CONFIGURED`，SDK 缺失时报 `LLM_SDK_MISSING`。
- `tests/test_suggest_dispatch.py`（9 项）：preset 解析 / 请求覆盖 / `custom` 缺参 400 / 缺 key 503 / StepFun 走 Anthropic、DeepSeek 走 OpenAI 协议分流。
- `tests/test_suggest_api.py`（5 项）：HTTP 边界，`/api/v1/ai/providers` 形状、`/api/v1/suggest` 新旧两路都能跑通。

完整测试 63 项全部通过。无网络、无 Key 即可跑。

### 风险与回滚

- 不动 `services/papers.py` 与前端的搜索语法，P1 的 per-token AND 行为不变。
- 若要回退，只需把 `from .ai_clients import ...` 与 `_resolve_provider` 替换成 P2-A 之前的写法，外部 API contract 无变化。

---

## P1 · 加载期去重 + DSL 修正 + per-token AND（已合并到 upstream）

通过三个单一职责的小 PR 分别提交，便于审查与回滚：

- **#95 PR-A** `feat(repo): 加载期按 Paper.id 兜底去重` — runtime defense layer。
- **#97 PR-B** `fix(web): DSL splitter 不再用字面引号包裹分词后的值` — 修正搜索栏将每个词重新加双引号的 bug。
- **#98 PR-C** `feat(search): per-token AND + 加权相关性打分` — 替换原 OR 匹配，标题命中权重最高。
