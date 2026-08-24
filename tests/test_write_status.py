import os
import json
import pathlib
import time
import pytest
from bibliome_mcp import server

# Helper to create a dummy db file
@pytest.fixture
def dummy_db(tmp_path):
    db_file = tmp_path / "embeddings.db"
    db_file.write_text("data")
    return db_file

# Patch find_embeddings_db to return dummy db
@pytest.fixture
def patched_find_db(monkeypatch, dummy_db):
    monkeypatch.setattr(server, "find_embeddings_db", lambda: dummy_db)
    return dummy_db

# Test initial write_status creates file with correct fields
@pytest.mark.asyncio
async def test_write_status_initial(patched_find_db):
    path = server.write_status()
    assert path is not None
    data = json.loads(path.read_text())
    assert data["pid"] == os.getpid()
    assert data["server_version"]
    assert data["started_at"] == data["last_seen_at"]
    assert data["last_tool"] is None
    assert data["tool_calls"] == 0

# Test tool_calls increments and last_tool updates
@pytest.mark.asyncio
async def test_write_status_tool_calls(patched_find_db):
    path1 = server.write_status("search_library")
    data1 = json.loads(path1.read_text())
    assert data1["tool_calls"] == 1
    assert data1["last_tool"] == "search_library"
    time.sleep(0.01)
    path2 = server.write_status("search_library")
    data2 = json.loads(path2.read_text())
    assert data2["tool_calls"] == 2
    assert data2["last_tool"] == "search_library"
    assert data2["started_at"] == data1["started_at"]

# Mirrors privatenote-mcp's test_the_status_file_is_never_left_half_written:
# the file is written to a .tmp path then atomically replace()'d, specifically
# so a reader (Bibliome's Settings pane) can never observe a half-written
# file. Assert valid JSON after EVERY call, not just at the end — a bug that
# only shows up on call N would be invisible to a single end-of-loop check.
@pytest.mark.asyncio
async def test_write_status_no_tmp_file(patched_find_db):
    dir_path = patched_find_db.parent
    for i in range(10):
        path = server.write_status("search_library")
        data = json.loads(path.read_text())  # raises if the file is truncated/partial
        assert data["tool_calls"] == i + 1
    assert not any(dir_path.glob("*.tmp"))

# Test write_status returns None when db not found
@pytest.mark.asyncio
async def test_write_status_no_db(monkeypatch):
    monkeypatch.setattr(server, "find_embeddings_db", lambda: None)
    assert server.write_status() is None

# Test write_status swallows errors when directory unwritable
#
# BUG FOUND IN THE FIRST DRAFT: this test used the raw `dummy_db` fixture
# without also depending on `patched_find_db`, so `find_embeddings_db()` was
# never monkeypatched — write_status() fell through to the REAL locator and
# wrote to this developer's actual production
# ~/Library/Containers/com.langberg.mypdflibrarian/.../mcp-status.json
# (confirmed: it did, during the first test run — the chmod below was also
# applied to the wrong, unrelated tmp directory). Using `patched_find_db`
# closes both holes: the write happens where the test thinks it does, and the
# chmod actually lands on the directory write_status will use.
@pytest.mark.asyncio
async def test_write_status_unwritable(monkeypatch, patched_find_db):
    dir_path = patched_find_db.parent
    dir_path.chmod(0o555)  # read+execute only
    try:
        result = server.write_status()
        assert result is None
    finally:
        dir_path.chmod(0o755)

# Test started_at resets when pid changes
@pytest.mark.asyncio
async def test_write_status_pid_change(monkeypatch, patched_find_db):
    # write initial status with original pid
    path1 = server.write_status()
    data1 = json.loads(path1.read_text())
    old_started = data1["started_at"]
    time.sleep(0.01)  # guarantee time.time() actually advances between calls
    # monkeypatch os.getpid to a different value
    monkeypatch.setattr(os, "getpid", lambda: 99999)
    path2 = server.write_status()
    data2 = json.loads(path2.read_text())
    assert data2["started_at"] != old_started
    assert data2["started_at"] > old_started
