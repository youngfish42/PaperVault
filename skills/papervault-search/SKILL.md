---
name: papervault-search
description: Search PaperVault research-paper metadata through its MCP server.
---

# PaperVault search

Use the `search_papers` MCP tool for literature discovery and metadata lookup.

## Tool

`search_papers` accepts:

- `query`: keywords or Web of Science style DSL, for example `TS=(privacy AND federated)`
- `conf`: conference names, such as `["NeurIPS", "ICLR"]`
- `author`: author filter
- `since`, `until`: inclusive publication years
- `page`: 1-based page number
- `size`: page size, at most 50

Present the returned title, authors, year, conference, abstract, and URL. Use the API's `total` value when describing result coverage. Refine the query or split broad searches into several calls when results are too large.

## MCP client setup

Configure the server as a stdio process:

```json
{"command":"python","args":["/path/to/PaperVault/mcp_server.py"]}
```
