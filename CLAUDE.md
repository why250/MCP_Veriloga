# CLAUDE.md — VerilogA MCP Server

Project context for AI coding assistants.

## What This Project Is

A **Model Context Protocol (MCP) server** that gives AI assistants (Cursor, Claude, etc.) search access to VerilogA documentation and a library of ready-to-use VerilogA code templates.

- Transport: HTTP SSE via FastMCP v2
- Default endpoint: `http://localhost:8097/mcp/sse`

## Repository Layout

```
MCP_Veriloga/
├── server/
│   ├── main.py          # FastMCP entry point; defines the 4 MCP tools
│   ├── indexer.py       # PDF + HTML parsing, TF-IDF + LSA (SVD) pipeline, FAISS + BM25 index build
│   ├── searcher.py      # Hybrid retrieval: 60% LSA/FAISS + 40% BM25
│   ├── templates.py     # 12 VerilogA code templates (resistor, NMOS, VCO, …)
│   ├── requirements.txt # Python dependencies
│   └── index_cache/     # Auto-generated at runtime (FAISS index, BM25 pickle, chunks.json)
├── reference/           # Source documents (PDFs + ADS 2025 HTML pages)
├── deploy/
│   ├── deploy_remote.sh      # One-shot deploy script for Linux servers
│   └── veriloga-mcp.service  # systemd unit file
└── README.md
```

## The 4 MCP Tools

| Tool | Purpose |
|---|---|
| `search_veriloga(query, top_k=5)` | Hybrid RAG search over all indexed docs |
| `show_page(source_file)` | Return full text of one document |
| `list_sources()` | List all indexed documents |
| `get_veriloga_template(model_type)` | Return a complete VerilogA module template |

## Embedding / Indexing Strategy

**No neural model, no internet required.** The semantic search uses:
- **TF-IDF + TruncatedSVD (LSA)** — `sklearn` pipeline, 256 latent dimensions
- **FAISS IndexFlatIP** — inner product (cosine after L2 normalisation)
- **BM25Okapi** — keyword index via `rank-bm25`

The pipeline is fit entirely from the local reference documents and saved to `server/index_cache/`. The first run builds the cache (~30 s); subsequent starts load from cache (~3 s).

## Running the Server

```bash
# build index then exit (optional, first query auto-triggers this)
python server/main.py --build-index

# start server (default port 8097)
python server/main.py

# custom port
python server/main.py --port 8096
```

## Cursor MCP Registration

In `%USERPROFILE%\.cursor\mcp.json` (Windows) or `~/.cursor/mcp.json` (Linux/Mac):

```json
{
  "mcpServers": {
    "veriloga-help": {
      "url": "http://localhost:8097/mcp/sse"
    }
  }
}
```

Restart Cursor after editing `mcp.json`.

## Key Implementation Notes

- `REFERENCE_DIR` is resolved relative to `indexer.py` location: `../reference/`
- `INDEX_CACHE_DIR` is `server/index_cache/` — add to `.gitignore` if large
- HTML parsing uses `lxml` as the BeautifulSoup parser; fall back to `html.parser` if lxml unavailable
- `alpha = 0.6` (semantic weight) in `searcher.py` — adjust for more BM25 vs semantic balance
- The server binds to `0.0.0.0` by default, making it accessible from other machines on the LAN
