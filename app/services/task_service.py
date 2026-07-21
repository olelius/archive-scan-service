"""扫描任务状态机和主进程单活动扫描互斥。"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from app.models.enums import TaskStatus
from app.models.records import ScanTaskRecord
from app.repositories.task_repository import TaskRepository


TASK_LIFECYCLE_LOCK = RLock()

_STARTABLE_STATUSES = frozenset(
    {
        TaskStatus.CREATED,
        TaskStatus.STOPPED,
        TaskStatus.FAILED,
        TaskStatus.COMPLETED,
    }
)


class TaskServiceError(RuntimeError):
    """任务服务错误基类。"""

    error_code = "TASK_ERROR"


class TaskNotFoundError(TaskServiceError):
    """任务不存在。"""

    error_code = "TASK_NOT_FOUND"

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"任务不存在：{task_id}")


class TaskAlreadyExistsError(TaskServiceError):
    """任务标识已经存在。"""

    error_code = "TASK_ALREADY_EXISTS"

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"任务已存在：{task_id}")


class TaskStateError(TaskServiceError):
    """任务状态不允许当前操作。"""

    error_code = "TASK_STATE_INVALID"

    def __init__(
        self,
        task_id: str,
        current_status: TaskStatus,
        target_status: TaskStatus,
    ) -> None:
        self.task_id = task_id
        self.current_status = current_status.value
        self.target_status = target_status.value
        super().__init__(
            f"任务 {task_id} 当前状态 {current_status.value} 不允许转换为 "
            f"{target_status.value}"
        )


class ScannerBusyError(TaskServiceError):
    """已有其他任务占用扫描仪。"""

    error_code = "SCANNER_BUSY"

    def __init__(self, active_task_id: str | None = None) -> None:
        self.active_task_id = active_task_id
        detail = (
            f"活动任务：{active_task_id}" if active_task_id is not None else ""
        )
        super().__init__(f"扫描仪正在被其他任务占用{detail}")


def _json_snapshot(value: Mapping[str, Any] | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} 必须是 JSON 对象")
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须只包含可序列化的 JSON 值") from exc


class TaskService:
    """管理任务生命周期；不直接加载 TWAIN，也不负责文件清理。"""

    def __init__(self, task_repository: TaskRepository) -> None:
        self._tasks = task_repository

    def create(
        self,
        task_id: str,
        device_id: str,
        *,
        device_snapshot: Mapping[str, Any] | None = None,
        capability_snapshot: Mapping[str, Any] | None = None,
        scan_params_snapshot: Mapping[str, Any] | None = None,
    ) -> ScanTaskRecord:
        """创建一个历史任务，不会清理或覆盖同标识任务。"""

        self._validate_identifier(task_id, "task_id")
        self._validate_identifier(device_id, "device_id")
        with TASK_LIFECYCLE_LOCK:
            if self._tasks.get(task_id) is not None:
                raise TaskAlreadyExistsError(task_id)
            device_snapshot_json = _json_snapshot(device_snapshot, "device_snapshot")
            capability_snapshot_json = _json_snapshot(
                capability_snapshot, "capability_snapshot"
            )
            scan_params_snapshot_json = _json_snapshot(
                scan_params_snapshot, "scan_params_snapshot"
            )
            try:
                return self._tasks.create(
                    task_id,
                    device_id,
                    device_snapshot_json=device_snapshot_json,
                    capability_snapshot_json=capability_snapshot_json,
                    scan_params_snapshot_json=scan_params_snapshot_json,
                )
            except sqlite3.IntegrityError as exc:
                raise TaskAlreadyExistsError(task_id) from exc

    def get(self, task_id: str) -> ScanTaskRecord | None:
        """读取一个任务，不改变状态。"""

        return self._tasks.get(task_id)

    def get_task(self, task_id: str) -> ScanTaskRecord | None:
        """`get` 的语义化别名，供上层接口组装使用。"""

        return self.get(task_id)

    def list_all(self) -> list[ScanTaskRecord]:
        """读取全部历史任务。"""

        return self._tasks.list_all()

    def list_tasks(self) -> list[ScanTaskRecord]:
        """`list_all` 的语义化别名，供上层接口组装使用。"""

        return self.list_all()

    def start_scan(
        self,
        task_id: str,
        settings: Mapping[str, Any],
    ) -> ScanTaskRecord:
        """开始或继续扫描，并原子抢占本机唯一活动扫描资格。"""

        with TASK_LIFECYCLE_LOCK:
            task = self._require(task_id)
            if task.status not in _STARTABLE_STATUSES:
                if task.status in {TaskStatus.SCANNING, TaskStatus.STOPPING}:
                    raise ScannerBusyError(task.task_id)
                raise TaskStateError(task_id, task.status, TaskStatus.SCANNING)
            params_json = _json_snapshot(settings, "settings")
            try:
                updated = self._tasks.claim_scan(
                    task_id,
                    scan_params_snapshot_json=params_json,
                )
            except KeyError as exc:
                raise TaskNotFoundError(task_id) from exc
            except ValueError as exc:
                current = self._require(task_id)
                raise TaskStateError(task_id, current.status, TaskStatus.SCANNING) from exc
            if updated is not None:
                return updated
            active = self._tasks.get_active()
            raise ScannerBusyError(active.task_id if active is not None else None)

    def stop_scan(self, task_id: str) -> ScanTaskRecord:
        """请求停止当前扫描；Worker 停止后再调用 `mark_stopped`。"""

        return self._transition(
            task_id,
            TaskStatus.STOPPING,
            allowed={TaskStatus.SCANNING},
        )

    def mark_stopped(self, task_id: str) -> ScanTaskRecord:
        """确认 Worker 已停止，保留已经登记的页面。"""

        return self._transition(
            task_id,
            TaskStatus.STOPPED,
            allowed={TaskStatus.STOPPING},
        )

    def complete_scan(self, task_id: str) -> ScanTaskRecord:
        """标记本轮扫描正常完成。"""

        return self._transition(
            task_id,
            TaskStatus.COMPLETED,
            allowed={TaskStatus.SCANNING},
        )

    def fail_scan(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> ScanTaskRecord:
        """标记扫描失败并保留稳定错误信息。"""

        if not error_code or not error_message:
            raise ValueError("扫描失败必须包含 error_code 和 error_message")
        return self._transition(
            task_id,
            TaskStatus.FAILED,
            allowed={TaskStatus.SCANNING, TaskStatus.STOPPING},
            error_code=error_code,
            error_message=error_message,
        )

    def cancel_scan(self, task_id: str) -> ScanTaskRecord:
        """显式取消尚未完成的任务；取消不会触发文件清理。"""

        return self._transition(
            task_id,
            TaskStatus.CANCELLED,
            allowed={
                TaskStatus.CREATED,
                TaskStatus.SCANNING,
                TaskStatus.STOPPING,
            },
        )

    def stop(self, task_id: str) -> ScanTaskRecord:
        """`stop_scan` 的简短别名。"""

        return self.stop_scan(task_id)

    def complete(self, task_id: str) -> ScanTaskRecord:
        """`complete_scan` 的简短别名。"""

        return self.complete_scan(task_id)

    def fail(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> ScanTaskRecord:
        """`fail_scan` 的简短别名。"""

        return self.fail_scan(task_id, error_code, error_message)

    def _transition(
        self,
        task_id: str,
        target: TaskStatus,
        *,
        allowed: set[TaskStatus],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ScanTaskRecord:
        with TASK_LIFECYCLE_LOCK:
            task = self._require(task_id)
            if task.status not in allowed:
                raise TaskStateError(task_id, task.status, target)
            return self._tasks.update_status(
                task_id,
                target,
                error_code=error_code,
                error_message=error_message,
            )

    def _require(self, task_id: str) -> ScanTaskRecord:
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    @staticmethod
    def _validate_identifier(value: str, field_name: str) -> None:
        if (
            not isinstance(value, str)
            or not value
            or value in {".", ".."}
            or Path(value).name != value
            or Path(value).anchor
        ):
            raise ValueError(f"{field_name} 必须是单段非空标识")


__all__ = [
    "ScannerBusyError",
    "TaskAlreadyExistsError",
    "TaskNotFoundError",
    "TaskService",
    "TaskServiceError",
    "TaskStateError",
]
