import asyncio
import json
import pytest
from mcp.client import Client
from bibliome_mcp import server

# NOTE: `server` here is the *module* (bibliome_mcp.server). The MCPServer
# instance the in-memory Client needs to connect to is the module-level
# `server.server` object built by @server.tool()'s decorator calls — passing
# the module itself to Client() raises "does not support the asynchronous
# context manager protocol" instead of connecting.
MCP_SERVER = server.server


# Helper to parse tool result content
async def get_tool_result(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.is_error
    payload = json.loads(result.content[0].text)
    return payload

@pytest.mark.asyncio
async def test_tools_advertised_and_schema(dummy_locators, patched_get_engine):
    """Verify that the server advertises exactly the three tools with correct schemas."""
    async with Client(MCP_SERVER) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert names == {"search_library", "ask_library", "library_status"}
        # Check schemas
        for t in tools.tools:
            assert t.description
            schema = t.input_schema
            if t.name == "search_library":
                assert "q" in schema["properties"], "q required"
                assert "k" in schema["properties"], "k optional"
                assert "q" in schema["required"], "q required"
                assert "k" not in schema.get("required", []), "k optional"
            elif t.name == "ask_library":
                assert "q" in schema["properties"], "q required"
                assert "k" in schema["properties"], "k optional"
                assert "q" in schema["required"], "q required"
                assert "k" not in schema.get("required", []), "k optional"
            elif t.name == "library_status":
                assert schema["properties"] == {}, "no args"
                # A zero-arg tool's schema has no "required" key at all
                # (confirmed against the real installed mcp 2.0.0) rather than
                # an empty list — assert the absence, not a specific shape.
                assert not schema.get("required"), "no required args"

@pytest.mark.asyncio
async def test_search_and_ask_happy_path(dummy_locators, patched_get_engine):
    """Happy path for search_library and ask_library using a fake engine."""
    fake_engine = patched_get_engine
    # set responses
    fake_engine.responses = {
        "search": {"results": [{"path": "/x.pdf", "page": 1, "score": 0.9, "snippet": "..."}]},
        "ask": {"answer": "42", "citations": [], "incomplete_sources": []},
    }
    async with Client(MCP_SERVER) as client:
        # search without k
        res = await client.call_tool("search_library", {"q": "foo"})
        assert not res.is_error
        payload = json.loads(res.content[0].text)
        assert payload == fake_engine.responses["search"]
        # check that engine called with k=20
        assert fake_engine.calls[-1] == ("search", {"q": "foo", "k": 20})
        # ask with explicit k
        res2 = await client.call_tool("ask_library", {"q": "bar", "k": 3})
        assert not res2.is_error
        payload2 = json.loads(res2.content[0].text)
        assert payload2 == fake_engine.responses["ask"]
        assert fake_engine.calls[-1] == ("ask", {"q": "bar", "k": 3})
        # ask without k must default to 5 (not left over from the k=3 call above)
        res3 = await client.call_tool("ask_library", {"q": "baz"})
        assert not res3.is_error
        assert fake_engine.calls[-1] == ("ask", {"q": "baz", "k": 5})

@pytest.mark.asyncio
async def test_library_status_happy_path(dummy_locators, patched_get_engine):
    """library_status returns engine health and found flags when everything is present."""
    fake_engine = patched_get_engine
    fake_engine.responses = {"health": {"doc_count": 10}}
    async with Client(MCP_SERVER) as client:
        res = await client.call_tool("library_status", {})
        assert not res.is_error
        payload = json.loads(res.content[0].text)
        assert payload["bibliome_found"] is True
        assert payload["db_found"] is True
        assert payload["engine_ok"] is True
        assert payload["doc_count"] == 10

@pytest.mark.asyncio
async def test_library_status_missing_app(monkeypatch, dummy_locators, patched_get_engine):
    """When Bibliome.app is missing, library_status reports hint and does not call engine."""
    monkeypatch.setattr(server, "find_bibliome_app", lambda: None)
    async with Client(MCP_SERVER) as client:
        res = await client.call_tool("library_status", {})
        payload = json.loads(res.content[0].text)
        assert payload["bibliome_found"] is False
        assert "hint" in payload
        # engine should not be called
        assert patched_get_engine.calls == []

@pytest.mark.asyncio
async def test_library_status_missing_db(monkeypatch, dummy_locators, patched_get_engine):
    """When embeddings.db is missing, library_status reports hint and does not call engine."""
    monkeypatch.setattr(server, "find_embeddings_db", lambda: None)
    async with Client(MCP_SERVER) as client:
        res = await client.call_tool("library_status", {})
        payload = json.loads(res.content[0].text)
        assert payload["db_found"] is False
        assert "hint" in payload
        assert patched_get_engine.calls == []

@pytest.mark.asyncio
async def test_search_library_runtime_error(monkeypatch, dummy_locators):
    """If _get_engine raises RuntimeError, search_library returns error dict."""
    monkeypatch.setattr(server, "_get_engine", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    async with Client(MCP_SERVER) as client:
        res = await client.call_tool("search_library", {"q": "foo"})
        payload = json.loads(res.content[0].text)
        assert payload["error"] == "boom"
        assert not res.is_error

@pytest.mark.asyncio
async def test_ask_library_runtime_error(monkeypatch, dummy_locators):
    """Same contract as search_library: ask_library must also surface a
    caught RuntimeError as a usable {"error": ...} payload, not a crash."""
    monkeypatch.setattr(server, "_get_engine", lambda: (_ for _ in ()).throw(RuntimeError("no engine")))
    async with Client(MCP_SERVER) as client:
        res = await client.call_tool("ask_library", {"q": "foo"})
        payload = json.loads(res.content[0].text)
        assert payload["error"] == "no engine"
        assert not res.is_error


@pytest.mark.asyncio
async def test_uncaught_exception_propagates_as_is_error(monkeypatch, dummy_locators):
    """If _get_engine raises a non-RuntimeError, the client sees is_error=True."""
    monkeypatch.setattr(server, "_get_engine", lambda: (_ for _ in ()).throw(ValueError("oops")))
    async with Client(MCP_SERVER) as client:
        res = await client.call_tool("search_library", {"q": "foo"})
        assert res.is_error
        assert "Error executing tool" in res.content[0].text

@pytest.mark.asyncio
async def test_library_status_engine_health_error(monkeypatch, dummy_locators, patched_get_engine):
    """If engine health call raises RuntimeError, library_status reports engine_ok False."""
    patched_get_engine.responses = {}
    def raise_health(op, params):
        raise RuntimeError("boom")
    patched_get_engine.call = raise_health
    async with Client(MCP_SERVER) as client:
        res = await client.call_tool("library_status", {})
        payload = json.loads(res.content[0].text)
        assert payload["engine_ok"] is False
        assert payload["error"] == "boom"
        assert payload["bibliome_found"] is True
        assert payload["db_found"] is True
