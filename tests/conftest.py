import os
import json
import pathlib
import sys
import pytest
from bibliome_mcp import server

# Autouse fixture to reset the module-level engine singleton before and after each test
@pytest.fixture(autouse=True)
def reset_engine():
    server._engine = None
    yield
    server._engine = None


# Engine.__init__ mutates global interpreter state: it inserts the app
# bundle's engine dir into sys.path and does `from embed_server import Index,
# dispatch`. Left unrestored, a later test's `import embed_server` would hit
# Python's module cache and silently get an EARLIER test's tmp_path module
# instead of its own — a stale-import bug that would be invisible from the
# test's own output. Snapshot and restore both around every test.
@pytest.fixture(autouse=True)
def _restore_interpreter_state():
    orig_path = list(sys.path)
    orig_modules = set(sys.modules.keys())
    yield
    sys.path[:] = orig_path
    for name in list(sys.modules.keys()):
        if name not in orig_modules:
            del sys.modules[name]

# Fixture that returns a simple fake engine with a call method
class FakeEngine:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []
    def call(self, op, params):
        self.calls.append((op, params))
        return self.responses.get(op, {})

@pytest.fixture
def fake_engine():
    return FakeEngine()

# Fixture that patches the locator functions to return dummy paths
@pytest.fixture
def dummy_locators(tmp_path, monkeypatch):
    # Create dummy app dir and db file
    app_dir = tmp_path / "Bibliome.app"
    app_dir.mkdir()
    # create the engine subdir and a dummy embed_server.py to satisfy Engine.__init__ if used
    engine_subdir = app_dir / server.ENGINE_SUBDIR
    engine_subdir.mkdir(parents=True)
    # create a minimal embed_server.py that defines Index and dispatch
    (engine_subdir / "embed_server.py").write_text(
        """
class Index:
    def __init__(self, db_path, _):
        pass

def dispatch(index, op, params):
    return {"op": op, "params": params}
"""
    )
    db_file = tmp_path / "embeddings.db"
    db_file.write_text("dummy")
    # patch the locator functions
    monkeypatch.setattr(server, "find_bibliome_app", lambda: app_dir)
    monkeypatch.setattr(server, "find_embeddings_db", lambda: db_file)
    return app_dir, db_file

# Fixture that patches _get_engine to return a fake engine
@pytest.fixture
def patched_get_engine(fake_engine, monkeypatch):
    monkeypatch.setattr(server, "_get_engine", lambda: fake_engine)
    return fake_engine

# Fixture that patches os.getpid for write_status tests
@pytest.fixture
def fake_pid(monkeypatch):
    monkeypatch.setattr(os, "getpid", lambda: 12345)
    return 12345
