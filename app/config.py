"""本机扫描服务的固定运行配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17653
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_MAX_LOG_FILE_SIZE_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 10


def _configured_origins() -> tuple[str, ...]:
    raw = os.environ.get("ARCHIVE_SCAN_ALLOWED_ORIGINS", "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(slots=True)
class Settings:
    """保存服务配置，并固定本机监听边界。"""

    data_root: Path | None = None
    host: str = field(default=DEFAULT_HOST, init=False)
    port: int = field(default=DEFAULT_PORT, init=False)
    log_level: str = DEFAULT_LOG_LEVEL
    max_log_file_size_bytes: int = DEFAULT_MAX_LOG_FILE_SIZE_BYTES
    log_backup_count: int = DEFAULT_LOG_BACKUP_COUNT
    allowed_origins: tuple[str, ...] = field(default_factory=_configured_origins)

    def __post_init__(self) -> None:
        if self.data_root is None:
            local_app_data = os.environ.get("LOCALAPPDATA")
            root = (
                Path(local_app_data) / "ArchiveScanService"
                if local_app_data
                else Path.home() / "AppData" / "Local" / "ArchiveScanService"
            )
        else:
            root = Path(self.data_root)
        self.data_root = root
        self.allowed_origins = tuple(self.allowed_origins)

    @property
    def database_path(self) -> Path:
        return self.data_root / "metadata.db"

    @property
    def originals_dir(self) -> Path:
        return self.data_root / "originals"

    @property
    def tasks_dir(self) -> Path:
        return self.data_root / "tasks"

    @property
    def thumbnails_dir(self) -> Path:
        return self.data_root / "thumbnails"

    @property
    def temp_dir(self) -> Path:
        return self.data_root / "temp"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    def ensure_directories(self) -> None:
        """创建运行所需目录，不扫描、清理或覆盖已有文件。"""

        for directory in (
            self.originals_dir,
            self.thumbnails_dir,
            self.tasks_dir,
            self.temp_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
