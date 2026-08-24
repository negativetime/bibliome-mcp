import os
import pathlib
import pytest
from bibliome_mcp import server

# Test Engine raises RuntimeError when embed_server.py missing
def test_engine_missing_file(tmp_path):
    app_dir = tmp_path / "Bibliome.app"
    app_dir.mkdir()
    # no embed_server.py
    db_file = tmp_path / "embeddings.db"
    db_file.write_text("data")
    with pytest.raises(RuntimeError) as exc:
        server.Engine(app_dir, db_file)
    assert "engine source not found" in str(exc.value)

# Test Engine raises RuntimeError when import fails
def test_engine_import_error(tmp_path):
    app_dir = tmp_path / "Bibliome.app"
    app_dir.mkdir()
    engine_subdir = app_dir / server.ENGINE_SUBDIR
    engine_subdir.mkdir(parents=True)
    # create embed_server.py that imports non-existent module
    (engine_subdir / "embed_server.py").write_text("import non_existent_module")
    db_file = tmp_path / "embeddings.db"
    db_file.write_text("data")
    with pytest.raises(RuntimeError) as exc:
        server.Engine(app_dir, db_file)
    assert "Import failed" in str(exc.value)
