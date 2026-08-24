import os
import json
import pathlib
import pytest
from bibliome_mcp import server

# This is the developer's own machine, and it really does have Bibliome.app
# installed in /Applications plus a real embeddings.db under the real home
# directory (Bibliome is his own shipped app) — the exact same absolute paths
# these locator functions look for. Without neutralizing them, a test like
# "returns None when nothing is installed" would silently start asserting
# facts about this one developer's disk instead of the code under test
# (confirmed: this bug was present in the first draft and made two tests
# pass/fail based on real machine state). autouse=True so every test in this
# file is isolated by default, whether or not it explicitly requests this
# fixture.
@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    # find_bibliome_app() also checks the absolute path /Applications/<name>
    # directly, independent of Path.home() — that check must be neutralized
    # separately or a real install there defeats the patch above.
    real_is_dir = pathlib.Path.is_dir
    def guarded_is_dir(self):
        if self == pathlib.Path("/Applications") / server.APP_BUNDLE_NAME:
            return False
        return real_is_dir(self)
    monkeypatch.setattr(pathlib.Path, "is_dir", guarded_is_dir)
    return tmp_path

# Test find_bibliome_app with env var
def test_find_bibliome_app_env_var(tmp_path, monkeypatch):
    app_dir = tmp_path / "Bibliome.app"
    app_dir.mkdir()
    monkeypatch.setenv("BIBLIOME_APP_DIR", str(app_dir))
    assert server.find_bibliome_app() == app_dir

# Test find_bibliome_app fallback to /Applications and ~/Applications
def test_find_bibliome_app_fallback(tmp_path, monkeypatch, fake_home):
    # create in fake home
    app_dir = fake_home / "Applications" / "Bibliome.app"
    app_dir.mkdir(parents=True)
    monkeypatch.delenv("BIBLIOME_APP_DIR", raising=False)
    assert server.find_bibliome_app() == app_dir

# Test find_bibliome_app returns None when not found
def test_find_bibliome_app_none(tmp_path, monkeypatch):
    monkeypatch.delenv("BIBLIOME_APP_DIR", raising=False)
    # ensure no /Applications or ~/Applications
    assert server.find_bibliome_app() is None

# Test find_embeddings_db env var
def test_find_embeddings_db_env_var(tmp_path, monkeypatch):
    db_file = tmp_path / "embeddings.db"
    db_file.write_text("data")
    monkeypatch.setenv("BIBLIOME_DB_PATH", str(db_file))
    assert server.find_embeddings_db() == db_file

# Test find_embeddings_db fallback paths
def test_find_embeddings_db_fallback(tmp_path, monkeypatch, fake_home):
    # create in first fallback path
    db_path = fake_home / "Library" / "Containers" / "com.langberg.mypdflibrarian" / "Data" / "Library" / "Application Support" / "PDFOrganizer" / "embeddings.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_text("data")
    monkeypatch.delenv("BIBLIOME_DB_PATH", raising=False)
    assert server.find_embeddings_db() == db_path

# Test find_embeddings_db returns None when not found
def test_find_embeddings_db_none(tmp_path, monkeypatch):
    monkeypatch.delenv("BIBLIOME_DB_PATH", raising=False)
    assert server.find_embeddings_db() is None
