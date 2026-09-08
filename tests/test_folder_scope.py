"""The `folder` parameter: scoping search_library / ask_library to one folder.

The engine's `paths` argument is an allow-list compared by whole path, and an
EMPTY list means "no scope" — so the two things worth pinning are that a folder
expands to exactly its files, and that a folder with nothing in it is an
error rather than a silent search of the entire library."""
import json
import os

import pytest
from mcp.client import Client

from bibliome_mcp import server

MCP_SERVER = server.server


def _library(tmp_path):
    notes = tmp_path / "Agent Notes"
    notes.mkdir()
    (notes / "Bibliome-status-a2085377.md").write_text("# Bibliome status\n1.5 is live.")
    (notes / "Cratekeeper-fix-29d4edb6.md").write_text("# Cratekeeper fix\nslice fix landed.")
    (notes / ".DS_Store").write_bytes(b"\x00")          # Finder litter, never a document
    (tmp_path / "Books").mkdir()
    (tmp_path / "Books" / "unrelated.pdf").write_bytes(b"%PDF")
    return notes


# --- _paths_under -------------------------------------------------------------

def test_folder_expands_to_its_own_files_only(tmp_path):
    notes = _library(tmp_path)
    paths = server._paths_under(str(notes))
    assert paths == sorted(os.path.normpath(str(p)) for p in
                           [notes / "Bibliome-status-a2085377.md",
                            notes / "Cratekeeper-fix-29d4edb6.md"])
    assert not any("unrelated.pdf" in p for p in paths)
    assert not any(".DS_Store" in p for p in paths)


def test_a_missing_folder_is_an_error_not_an_unscoped_search(tmp_path):
    with pytest.raises(RuntimeError, match="Not a folder"):
        server._paths_under(str(tmp_path / "does-not-exist"))


def test_an_empty_folder_is_an_error_not_an_unscoped_search(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(RuntimeError, match="No files"):
        server._paths_under(str(tmp_path / "empty"))


def test_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _library(tmp_path)
    assert server._paths_under("~/Agent Notes")


# --- Over the protocol ---------------------------------------------------------

@pytest.mark.asyncio
async def test_folder_is_advertised_as_optional_on_both_tools(dummy_locators, patched_get_engine):
    async with Client(MCP_SERVER) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        for name in ("search_library", "ask_library"):
            schema = tools[name].input_schema
            assert "folder" in schema["properties"]
            assert "folder" not in schema.get("required", [])


@pytest.mark.asyncio
async def test_search_with_folder_passes_the_expanded_paths(tmp_path, dummy_locators, patched_get_engine):
    notes = _library(tmp_path)
    patched_get_engine.responses = {"search": {"results": []}}
    async with Client(MCP_SERVER) as client:
        res = await client.call_tool("search_library", {"q": "status", "folder": str(notes)})
        assert not res.is_error
    op, params = patched_get_engine.calls[-1]
    assert op == "search"
    assert params["q"] == "status" and params["k"] == 20
    assert params["paths"] == server._paths_under(str(notes))


@pytest.mark.asyncio
async def test_ask_with_folder_passes_the_expanded_paths(tmp_path, dummy_locators, patched_get_engine):
    notes = _library(tmp_path)
    patched_get_engine.responses = {"ask": {"answer": "x", "citations": [], "incomplete_sources": []}}
    async with Client(MCP_SERVER) as client:
        res = await client.call_tool("ask_library", {"q": "what shipped", "folder": str(notes)})
        assert not res.is_error
    op, params = patched_get_engine.calls[-1]
    assert op == "ask"
    assert params["paths"] == server._paths_under(str(notes))


@pytest.mark.asyncio
async def test_without_folder_no_paths_key_is_sent(dummy_locators, patched_get_engine):
    """The unscoped call must be byte-for-byte what it was before `folder`
    existed — the engine defaults differ when a key is absent vs None."""
    patched_get_engine.responses = {"search": {"results": []}}
    async with Client(MCP_SERVER) as client:
        await client.call_tool("search_library", {"q": "foo"})
    assert patched_get_engine.calls[-1] == ("search", {"q": "foo", "k": 20})


@pytest.mark.asyncio
async def test_a_bad_folder_comes_back_as_a_clean_error(tmp_path, dummy_locators, patched_get_engine):
    async with Client(MCP_SERVER) as client:
        res = await client.call_tool("search_library",
                                     {"q": "foo", "folder": str(tmp_path / "nope")})
        assert not res.is_error                       # a normal reply, not a protocol failure
        payload = json.loads(res.content[0].text)
    assert "Not a folder" in payload["error"]
    assert patched_get_engine.calls == [], "the engine must not have been searched unscoped"
