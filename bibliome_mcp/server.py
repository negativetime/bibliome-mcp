"""MCP stdio server exposing Bibliome's local PDF search/RAG engine to any MCP client.

Bibliome.app ships its search/RAG engine as plain Python source inside its own
app bundle (Contents/Resources/pdf_organizer/) — this module imports that
source directly, in-process, using the caller's own Python environment. It
deliberately does NOT vendor or duplicate that source into this package, and
it does NOT try to run Bibliome's own bundled interpreter as a subprocess:
that binary is sandbox-entitled (com.apple.security.inherit) to run only as a
child of the Bibliome.app process itself — any external launch of it is
killed by macOS (SIGTRAP) before it starts.

Because the engine's own dependencies (fastembed/numpy always, mlx optionally
for on-device RAG answers) then run in *this* process, they need to be
installed here too — see the [ask-mlx] / [ask-ollama] extras in pyproject.toml.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from mcp.server.mcpserver import MCPServer

APP_BUNDLE_NAME = "Bibliome.app"

# Read from package metadata rather than restated here: this string is now
# shown to users in Bibliome's Connections pane, and a hand-kept copy would
# eventually advertise a version that was never published. The literal is only
# the fallback for running from a source checkout that was never installed.
try:
    from importlib.metadata import version as _pkg_version
    SERVER_VERSION = _pkg_version("bibliome-mcp")
except Exception:                                   # not installed
    SERVER_VERSION = "0.3.0"
ENGINE_SUBDIR = "Contents/Resources/pdf_organizer"


# --- Locator helpers ---------------------------------------------------------

def find_bibliome_app() -> Optional[Path]:
    """Locate the installed Bibliome.app bundle.

    Honors $BIBLIOME_APP_DIR; otherwise checks /Applications then ~/Applications.
    """
    env = os.environ.get("BIBLIOME_APP_DIR")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p
    for c in (Path("/Applications") / APP_BUNDLE_NAME, Path.home() / "Applications" / APP_BUNDLE_NAME):
        if c.is_dir():
            return c
    return None


def find_embeddings_db() -> Optional[Path]:
    """Locate the user's embeddings.db.

    Honors $BIBLIOME_DB_PATH; otherwise checks, in order: the Mac App Store
    sandbox container, the direct-download support dir, then the legacy
    ~/.pdf-organizer fallback.
    """
    env = os.environ.get("BIBLIOME_DB_PATH")
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    home = Path.home()
    for c in (
        home / "Library/Containers/com.langberg.mypdflibrarian/Data/Library/Application Support/PDFOrganizer/embeddings.db",
        home / "Library/Application Support/PDFOrganizer/embeddings.db",
        home / ".pdf-organizer/embeddings.db",
    ):
        if c.is_file():
            return c
    return None


# --- Engine ------------------------------------------------------------------

class Engine:
    """Imports Bibliome's own engine straight out of the installed app bundle
    and wraps its dispatch(). Built once, on first real tool call — never at
    module import time, so the MCP server always starts cleanly even without
    Bibliome installed."""

    def __init__(self, app_dir: Path, db_path: Path):
        engine_dir = app_dir / ENGINE_SUBDIR
        if not (engine_dir / "embed_server.py").is_file():
            raise RuntimeError(
                f"Bibliome's engine source not found at {engine_dir}. "
                "The Bibliome.app install may be incomplete or outdated."
            )

        # The same env var the Bibliome Mac app itself sets for this exact
        # engine (TaskRunner.swift / EmbeddingService.swift) — keeps every
        # internal app_paths.data_dir() call (e.g. ocr_store's OCR sidecar
        # lookup) pointed at the SAME directory as the embeddings.db we
        # located, instead of silently falling back to ~/.pdf-organizer.
        os.environ.setdefault("PDF_DATA_DIR", str(db_path.parent))

        if str(engine_dir) not in sys.path:
            sys.path.insert(0, str(engine_dir))
        try:
            from embed_server import Index, dispatch  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "Bibliome's engine needs 'fastembed' and 'numpy' in this "
                f"Python environment (pip install bibliome-mcp). Import failed: {e}"
            ) from e

        self._dispatch = dispatch
        self._index = Index(db_path, None)

    def call(self, op: str, params: dict) -> dict:
        return self._dispatch(self._index, op, params)


STATUS_FILENAME = "mcp-status.json"


def write_status(tool: Optional[str] = None,
                 db_path: Optional[Path] = None) -> Optional[Path]:
    """Record that this server is running, next to the database it serves.

    Bibliome.app's Settings pane reads this to answer "is anything connected?"
    — a question it cannot answer any other way. A stdio MCP server is spawned
    and killed by its client, so there is no port to probe and no daemon to
    ask; the only evidence a client ever attached is a mark the server leaves
    behind. The file lands beside embeddings.db, which for a Mac App Store
    install is inside the app's own container — the one place the sandboxed
    app is allowed to read without the user granting anything.

    Best-effort by design: a read-only or missing directory must never take
    the server down, so every failure here is swallowed. The server's job is
    to answer queries, not to report on itself.
    """
    try:
        db = db_path or find_embeddings_db()
        if db is None:
            return None
        path = db.parent / STATUS_FILENAME
        now = time.time()
        prior: dict = {}
        try:
            prior = json.loads(path.read_text())
        except (OSError, ValueError):
            pass
        # started_at survives across calls so the pane can show a session
        # length; it is reset only when a different pid takes over.
        started = prior.get("started_at") if prior.get("pid") == os.getpid() else None
        payload = {
            "pid": os.getpid(),
            "server_version": SERVER_VERSION,
            "started_at": started or now,
            "last_seen_at": now,
            "last_tool": tool or prior.get("last_tool"),
            "last_tool_at": now if tool else prior.get("last_tool_at"),
            "tool_calls": prior.get("tool_calls", 0) + (1 if tool else 0),
            "db_path": str(db),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(path)          # atomic: the app may read mid-write
        return path
    except Exception:
        return None


_engine: Optional[Engine] = None


def _get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    app_dir = find_bibliome_app()
    db_path = find_embeddings_db()
    if app_dir is None:
        raise RuntimeError(
            "Bibliome.app was not found. Install it from the Mac App Store, "
            "or set BIBLIOME_APP_DIR to point at the .app bundle."
        )
    if db_path is None:
        raise RuntimeError(
            "No Bibliome embeddings database was found. Open Bibliome.app and "
            "index a library first, or set BIBLIOME_DB_PATH to point at the "
            "embeddings.db file."
        )
    _engine = Engine(app_dir, db_path)
    return _engine


# --- MCP server --------------------------------------------------------------

server = MCPServer(
    "bibliome-library",
    version=SERVER_VERSION,
    instructions=(
        "Read-only access to the user's local Bibliome PDF library, running "
        "fully on-device inside Bibliome's own search/RAG engine — nothing "
        "leaves the machine. The user must have Bibliome.app installed and "
        "have indexed at least one PDF library for these tools to return "
        "anything useful."
    ),
)


@server.tool()
def search_library(q: str, k: int = 20) -> dict:
    """Hybrid FTS+vector search, reranked, over the user's local Bibliome PDF
    library. Returns up to `k` matching passages: path, page, score, snippet.
    Read-only and fully on-device. Call `library_status` first if results
    look wrong or empty."""
    write_status("search_library")
    try:
        return _get_engine().call("search", {"q": q, "k": k})
    except RuntimeError as e:
        return {"error": str(e)}


@server.tool()
def ask_library(q: str, k: int = 5) -> dict:
    """RAG-answer a question from the user's Bibliome PDF library, entirely
    on-device. Returns {answer, citations, incomplete_sources}. Needs an
    on-device answer provider installed (mlx on Apple Silicon by default, or
    set PDF_ASK_PROVIDER=ollama with a local Ollama daemon running) — see the
    README's ask-mlx / ask-ollama extras. Prefer search_library if you just
    need the underlying passages."""
    write_status("ask_library")
    try:
        return _get_engine().call("ask", {"q": q, "k": k})
    except RuntimeError as e:
        return {"error": str(e)}


@server.tool()
def library_status() -> dict:
    """Report whether Bibliome.app and its embeddings database were located,
    plus a live engine health check (doc/chunk counts). Run this first when
    search_library / ask_library return errors or nothing."""
    app_dir = find_bibliome_app()
    db_path = find_embeddings_db()
    write_status("library_status", db_path)
    status: dict = {
        "bibliome_found": app_dir is not None,
        "app_dir": str(app_dir) if app_dir else None,
        "db_found": db_path is not None,
        "db_path": str(db_path) if db_path else None,
    }
    if not status["bibliome_found"]:
        status["hint"] = (
            "Bibliome.app not found in /Applications or ~/Applications. "
            "Install it from the Mac App Store, or set BIBLIOME_APP_DIR."
        )
        return status
    if not status["db_found"]:
        status["hint"] = (
            "No embeddings.db found. Open Bibliome.app and index a library, "
            "or set BIBLIOME_DB_PATH."
        )
        return status
    try:
        health = _get_engine().call("health", {})
        status.update(health)
        status["engine_ok"] = True
    except RuntimeError as e:
        status["engine_ok"] = False
        status["error"] = str(e)
    return status


# --- Entry point -------------------------------------------------------------

def main() -> None:
    """Console-script entry point. Runs the MCP server on stdio."""
    # Before run(): a client that connects and lists tools without calling one
    # is still a client, and the pane should say so.
    write_status()
    server.run()


if __name__ == "__main__":
    main()
