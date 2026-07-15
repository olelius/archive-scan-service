from pathlib import Path

import pytest


def test_settings_are_loopback_only(tmp_path: Path):
    from app.config import Settings

    settings = Settings(data_root=tmp_path)

    assert settings.data_root == tmp_path
    assert settings.host == "127.0.0.1"
    assert settings.port == 17653
    assert settings.database_path == tmp_path / "metadata.db"
    assert settings.originals_dir == tmp_path / "originals"
    assert settings.thumbnails_dir == tmp_path / "thumbnails"
    assert settings.temp_dir == tmp_path / "temp"
    assert settings.logs_dir == tmp_path / "logs"


def test_settings_default_root_uses_localappdata(monkeypatch, tmp_path: Path):
    from app.config import Settings

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    settings = Settings()

    assert settings.data_root == tmp_path / "ArchiveScanService"


def test_ensure_directories_creates_runtime_directories_without_cleaning_files(
    tmp_path: Path,
):
    from app.config import Settings

    existing_file = tmp_path / "originals" / "existing.jpg"
    existing_file.parent.mkdir()
    existing_file.write_bytes(b"keep")
    settings = Settings(data_root=tmp_path)

    settings.ensure_directories()

    for directory in (
        settings.originals_dir,
        settings.thumbnails_dir,
        settings.temp_dir,
        settings.logs_dir,
    ):
        assert directory.is_dir()
    assert existing_file.read_bytes() == b"keep"
    assert not settings.database_path.exists()


def test_settings_do_not_accept_a_custom_host_or_port(tmp_path: Path):
    from app.config import Settings

    with pytest.raises(TypeError):
        Settings(data_root=tmp_path, host="0.0.0.0")
    with pytest.raises(TypeError):
        Settings(data_root=tmp_path, port=8080)
