# PaperVault Search Skill

这是 PaperVault 的可下载 Skill 包，供支持 MCP 的 ChatGPT、Claude Desktop、Cursor 等客户端使用。

## 使用步骤

1. 解压本目录到本地。
2. 确保同级 PaperVault 仓库包含 `mcp_server.py` 和 `cache/cache.jsonl.gz`。
3. 在 MCP 客户端中添加 stdio server：

```json
{"command":"python","args":["/绝对路径/PaperVault/mcp_server.py"]}
```

4. 让智能体调用 `search_papers`。

完整参数、DSL 示例和结果展示约定见 `SKILL.md`。Skill 本身不包含会持续更新的论文缓存；缓存仍由 PaperVault 仓库或 Hugging Face Dataset 提供。
