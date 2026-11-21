---
name: papervault-search
description: Search PaperVault research-paper metadata through its MCP server.
---

# PaperVault 搜索 Skill

这个 Skill 让智能体通过 PaperVault 的 `search_papers` MCP 工具检索论文元数据。适合文献发现、作者/会议追踪和按年份筛选；结果来自 PaperVault 的公开论文元数据库。

## 什么时候使用

当用户要查找 AI、机器学习、自然语言处理、计算机视觉等方向的论文，或需要按会议、作者、年份缩小结果范围时，调用 `search_papers`。

## 工具参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `query` | string | 关键词或 Web of Science 风格 DSL，例如 `TS=(privacy AND federated)` |
| `conf` | string[] | 会议/期刊系列，例如 `["NeurIPS", "ICLR"]` |
| `author` | string | 作者名过滤 |
| `since` / `until` | integer | 包含边界的年份范围 |
| `page` | integer | 从 1 开始，默认 1 |
| `size` | integer | 每页数量，最大 50 |

只传需要的参数。宽泛查询请分批分页，使用返回的 `total` 说明覆盖范围。

## 查询示例

- `TS=(privacy AND federated)`：主题同时包含两个词
- `TI="diffusion model" PY=2024-2026`：标题短语 + 年份范围
- `AU="Yann LeCun" SO=NeurIPS`：作者 + 会议
- `TS=robotics NOT survey`：排除综述

支持字段 `TS`（主题）、`TI`（标题）、`AB`（摘要）、`AU`（作者）、`SO`（会议）和 `PY`（年份），以及 `AND`、`OR`、`NOT`、`NEAR/x`、括号、引号短语和逗号多值。

## 回复结果

向用户展示标题、作者、年份、会议、摘要和原文 URL。结果很多时先概括 `total`，再给出当前页，并建议进一步限定关键词、会议或年份。不要把内部字段名当作用户可见标题。

## MCP 客户端配置

下载并解压本 Skill 后，在 MCP 客户端中把 `mcp_server.py` 配置为 stdio server：

```json
{
  "command": "python",
  "args": ["/path/to/PaperVault/mcp_server.py"]
}
```

如果使用仓库外的路径，请把 `/path/to/PaperVault` 替换为实际 PaperVault 目录。服务端读取本地 `cache/cache.jsonl.gz`；也可以通过 `PAPERVAULT_CACHE_PATH` 指定缓存文件。
