"""扫描任务和页面的不可变记录模型。"""

from dataclasses import dataclass

from .enums import TaskStatus


@dataclass(frozen=True, slots=True)
class ScanTaskRecord:
    """SQLite 中的一条扫描任务记录。"""

    task_id: str
    device_id: str
    status: TaskStatus
    created_at: str
    updated_at: str
    last_page_sequence: int = 0
    error_code: str | None = None
    error_message: str | None = None
    device_snapshot_json: str | None = None
    capability_snapshot_json: str | None = None
    scan_params_snapshot_json: str | None = None


@dataclass(frozen=True, slots=True)
class ScanPageRecord:
    """SQLite 中的一条扫描页面记录。"""

    page_id: str
    task_id: str
    sequence: int
    original_path: str
    thumbnail_path: str
    sha256: str
    file_size: int
    created_at: str
    width: int | None = None
    height: int | None = None
