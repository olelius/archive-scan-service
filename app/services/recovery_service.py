"""服务启动时扫描任务恢复。"""

from __future__ import annotations

from app.models.enums import TaskStatus
from app.models.records import ScanTaskRecord
from app.repositories.task_repository import TaskRepository

from .task_service import TASK_LIFECYCLE_LOCK


class RecoveryService:
    """把服务重启时遗留的活动任务安全恢复为停止状态。"""

    RESTART_ERROR_CODE = "SERVICE_RESTARTED"
    RESTART_ERROR_MESSAGE = "服务重启后停止未完成扫描"

    def __init__(self, task_repository: TaskRepository) -> None:
        self._tasks = task_repository

    def recover(self) -> list[ScanTaskRecord]:
        """恢复全部历史任务，不启动设备、不删除页面或文件。"""

        with TASK_LIFECYCLE_LOCK:
            recovered: list[ScanTaskRecord] = []
            for task in self._tasks.list_all():
                if task.status in {TaskStatus.SCANNING, TaskStatus.STOPPING}:
                    task = self._tasks.update_status(
                        task.task_id,
                        TaskStatus.STOPPED,
                        error_code=self.RESTART_ERROR_CODE,
                        error_message=self.RESTART_ERROR_MESSAGE,
                    )
                recovered.append(task)
            return recovered

    def recover_on_startup(self) -> list[ScanTaskRecord]:
        """`recover` 的启动语义别名。"""

        return self.recover()


__all__ = ["RecoveryService"]
