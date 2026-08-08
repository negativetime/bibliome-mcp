# bibliome-mcp

an MCP (Model Context Protocol) server that lets any MCP client — claude code, claude desktop, codex — search and ask questions over your **local Bibliome PDF library**. it imports Bibliome's own on-device search/RAG engine straight out of your installed Bibliome.app, so whatever you indexed in the Mac app is exactly what this exposes.

## requirements (read this first)

this connector doesn't vendor a search engine and doesn't index anything — it reads what's already there.

you must already have:

1. **Bibliome.app installed** on this mac — [Mac App Store](https://apps.apple.com/us/app/bibliome-library/id6786826590?mt=12) — bundle id `com.langberg.mypdflibrarian`. it must live in `/Applications/Bibliome.app` (or `~/Applications/Bibliome.app`).
2. **at least one library already indexed** in Bibliome. the app builds `embeddings.db` itself; this connector only reads from it. if you've never opened Bibliome and indexed PDFs, there's nothing for it to search.

if both are true, `search_library` and `library_status` work with no extra configuration. `ask_library` needs one more thing — see below.

## install

```sh
pip install bibliome-mcp
```

this pulls in `fastembed` + `numpy` (the same lightweight, no-torch embedding stack Bibliome's own engine uses) — they run search/rerank in *this* process, not inside the app. no Apple Silicon requirement for search.

for `ask_library` (RAG answers), also install one answer provider:

```sh
# on-device via Apple MLX (Apple Silicon only — matches Bibliome's own default)
pip install "bibliome-mcp[ask-mlx]"

# or: a local Ollama daemon instead (works on Intel too)
pip install "bibliome-mcp[ask-ollama]"
export PDF_ASK_PROVIDER=ollama   # must also be set at runtime, not just installed
```

without either, `ask_library` still returns a graceful "couldn't start the local model" message with supporting citations instead of erroring — `search_library` is unaffected either way.

then `bibliome-mcp` is on your PATH and ready to be wired into a client.

## setup

### (a) claude code

```sh
claude mcp add bibliome-library -s user -- bibliome-mcp
```

### (b) claude desktop

add this to `claude_desktop_config.json` (under the `mcpServers` block):

```json
{
  "mcpServers": {
    "bibliome-library": {
      "command": "bibliome-mcp"
    }
  }
}
```

### (c) codex

add this to `~/.codex/config.toml`:

```toml
[mcp_servers.bibliome-library]
command = "bibliome-mcp"
```

## tools

- **search_library** `(q, k=20)` — semantic search over your local PDF library; returns up to `k` matching passages.
- **ask_library** `(q, k=5)` — ask a question, get an on-device RAG answer grounded in your PDFs (needs `ask-mlx` or `ask-ollama`, see Install).
- **library_status** `()` — reports whether Bibliome.app and an `embeddings.db` were found, plus a live engine health check. run this first when something looks wrong.

## troubleshooting

**"Bibliome not found" / `bibliome_found: false`**
Bibliome.app must be at `/Applications/Bibliome.app` or `~/Applications/Bibliome.app`. if it's elsewhere, point at it explicitly:

```sh
export BIBLIOME_APP_DIR="/Volumes/External/Bibliome.app"
```

**no results / `db_found: false`**
open Bibliome.app and index at least one PDF library — the app builds `embeddings.db` itself. if your `embeddings.db` is in a non-default location, point at it:

```sh
export BIBLIOME_DB_PATH="/path/to/embeddings.db"
```

## how it works

this connector does **not** reimplement search and does **not** vendor any of Bibliome's proprietary code — it imports it directly from your installed copy, in-process, on the first tool call: it adds `Bibliome.app/Contents/Resources/pdf_organizer` (plain Python source, shipped inside every install) to `sys.path` and calls the engine's own `Index`/`dispatch` directly, pointed at your `embeddings.db`.

this is deliberately **not** a subprocess bridge to Bibliome's bundled interpreter — that binary carries the macOS sandbox entitlement `com.apple.security.inherit`, meaning it will only run as a child of the Bibliome.app process itself; any external launcher (including this one) gets killed by the sandbox before it starts. running the plain-source engine in-process, with fastembed/numpy installed in *this* environment, sidesteps that entirely.

because it's literally Bibliome's own engine code answering, search and RAG results always match what the app itself would produce.

## privacy

everything stays local. no network calls, no telemetry, no data leaving your machine — search and ask both run fully on-device.
