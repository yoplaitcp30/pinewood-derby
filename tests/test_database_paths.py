from pathlib import Path

import pinewood_derby.database as database_module
from pinewood_derby.database import Database, default_database_path


def test_windows_database_path_uses_localappdata_and_creates_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(database_module.sys, "platform", "win32")
    local_app_data = tmp_path / "Local App Data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    path = default_database_path()
    assert path == local_app_data / "pinewood-derby" / "events.sqlite3"
    assert not path.parent.exists()

    database = Database(path)
    try:
        assert path.is_file()
    finally:
        database.close()


def test_windows_database_path_falls_back_when_localappdata_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(database_module.sys, "platform", "win32")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "profile")
    monkeypatch.setenv("LOCALAPPDATA", "")

    assert default_database_path() == (
        tmp_path / "profile" / "AppData" / "Local" / "pinewood-derby" / "events.sqlite3"
    )
