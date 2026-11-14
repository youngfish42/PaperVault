"""Minimal stdio MCP server for PaperVault search.

Run with ``python mcp_server.py``. It deliberately uses only the Python
standard library and the existing PaperRepository, so it works alongside the
Flask service without another network hop.
"""
import json
import os
import sys
from pathlib import Path

from papervault.services.papers import PaperRepository, SearchCriteria

ROOT = Path(__file__).resolve().parent
repo = PaperRepository(
    Path(os.environ.get("PAPERVAULT_CACHE_PATH", ROOT / "cache/cache.jsonl.gz")),
    refresh_on_load=False,
)

TOOLS = [{"name": "search_papers", "description": "Search PaperVault research-paper metadata.",
          "inputSchema": {"type": "object", "properties": {
              "query": {"type": "string"}, "conf": {"type": "array", "items": {"type": "string"}},
              "author": {"type": "string"}, "since": {"type": "integer"}, "until": {"type": "integer"},
              "page": {"type": "integer", "minimum": 1}, "size": {"type": "integer", "minimum": 1, "maximum": 50}},
              "additionalProperties": False}}]

def handle(msg):
    method, params, ident = msg.get("method"), msg.get("params", {}), msg.get("id")
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "papervault", "version": "1.0"}}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call" and params.get("name") == "search_papers":
        args = params.get("arguments", {})
        criteria = SearchCriteria(query=args.get("query", ""), confs=args.get("conf", []), author=args.get("author"),
                                   since=args.get("since"), until=args.get("until"), page=args.get("page", 1), size=min(args.get("size", 10), 50))
        items, total = repo.search(criteria)
        data = {"items": [p.__dict__ if hasattr(p, "__dict__") else {"id": p.id, "conf": p.conf, "year": p.year, "title": p.title, "url": p.url, "authors": p.authors, "abstract": p.abstract, "code": p.code} for p in items], "total": total}
        return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}
    if ident is not None:
        return {"error": {"code": -32601, "message": "Method not found"}}
    return None

for line in sys.stdin:
    try:
        msg = json.loads(line); result = handle(msg)
        if msg.get("id") is not None and result is not None:
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
    except Exception as exc:
        if "id" in locals() and msg.get("id") is not None:
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(exc)}}), flush=True)
