# PaperVault 搜索 SDK 文档

## 下载 Skill

如果你使用支持 MCP 的智能体客户端，可直接下载 [PaperVault Search Skill ZIP](../web-vue/public/downloads/papervault-search-skill.zip)，解压后按包内 `SKILL.md` 配置即可。

本文介绍 PaperVault 的搜索 API，适合脚本、Notebook 和第三方应用集成。服务以论文**元数据**为主，基础搜索无需登录或 API key。

## 快速开始

```bash
curl -G 'https://papervault.top/api/v1/papers' \\
  --data-urlencode 'q=TS=(federated AND privacy) SO=ICLR PY=2024-2026' \\
  --data-urlencode 'page=1' --data-urlencode 'page_size=20'
```

`q` 使用 WoS 风格 DSL。字段包括 `TS`（主题）、`TI`（标题）、`AB`（摘要）、`AU`（作者）、`SO`（会议）和 `PY`（年份）。支持 `AND`、`OR`、`NOT`、`NEAR/x`、括号、引号短语、逗号多值和年份范围。

## 响应

成功响应为 `{ "items": [...], "meta": { "page": 1, "page_size": 20, "total": 123 } }`。页码从 1 开始，`page_size` 受服务端上限约束。错误统一返回 `error`、`message` 和 `request_id`，可据此重试或定位日志。

## JavaScript / Python

```js
const params = new URLSearchParams({ q: 'TI=diffusion AND PY=2024', page: '1', page_size: '20' });
const response = await fetch(`/api/v1/papers?${params}`);
const { items, meta } = await response.json();
```

```python
import requests
r = requests.get('https://papervault.top/api/v1/papers', params={
    'q': 'AU="Yann LeCun" SO=NeurIPS', 'page': 1, 'page_size': 20,
})
r.raise_for_status()
data = r.json()
```

## AI 接口（可选）

`POST /api/v1/suggest` 根据研究描述生成关键词；`POST /api/v1/ai/rerank` 对候选论文按相关度重排。请求可携带 provider、model、base_url、api_key 等覆盖项，也可使用服务端环境变量。API key 仅应在服务端或短生命周期会话中传递。

## 兼容性

API 路径统一使用 `/api/v1` 前缀。客户端应忽略未知响应字段，并在收到 429 或 5xx 时采用指数退避。语义搜索上线后将沿用相同的分页和错误封装。

## MCP

PaperVault also includes a dependency-free stdio MCP server. Configure an MCP client to run:

```json
{"command":"python","args":["/path/to/PaperVault/mcp_server.py"]}
```

The `search_papers` tool accepts `query`, `conf`, `author`, `since`, `until`, `page`, and `size`.

智能体可直接加载仓库中的 [`skills/papervault-search/SKILL.md`](../skills/papervault-search/SKILL.md) 获取调用规范。
