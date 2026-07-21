"""扫描任务 SQLite 仓储。"""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from app.models.enums import TaskStatus
from app.models.records import ScanTaskRecord

from .database import Database


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _status_value(status: TaskStatus | str) -> str:
    return TaskStatus(status).value


def _optional_text(value: str | None) -> str | None:
    return value or None


class TaskRepository:
    """提供任务记录的事务读写操作。"""

    _ACTIVE_STATUS_VALUES = (TaskStatus.SCANNING.value, TaskStatus.STOPPING.value)
    _RESUMABLE_STATUS_VALUES = (
        TaskStatus.CREATED.value,
        TaskStatus.STOPPED.value,
        TaskStatus.FAILED.value,
        TaskStatus.COMPLETED.value,
    )

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(
        self,
        task_id: str,
        device_id: str,
        *,
        status: TaskStatus | str = TaskStatus.CREATED,
        created_at: str | None = None,
        device_snapshot_json: str | None = None,
        capability_snapshot_json: str | None = None,
        scan_params_snapshot_json: str | None = None,
    ) -> ScanTaskRecord:
        timestamp = created_at or _utc_now()
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO scan_task (
                    task_id,
                    device_id,
                    status,
                    device_snapshot_json,
                    capability_snapshot_json,
                    scan_params_snapshot_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    device_id,
                    _status_value(status),
                    device_snapshot_json,
                    capability_snapshot_json,
                    scan_params_snapshot_json,
                    timestamp,
                    timestamp,
                ),
            )
        record = self.get(task_id)
        if record is None:
            raise RuntimeError(f"任务 {task_id} 写入后无法读取")
        return record

    def get(self, task_id: str) -> ScanTaskRecord | None:
        with self._database.lock:
            row = self._database.connection.execute(
                "SELECT * FROM scan_task WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list_all(self) -> list[ScanTaskRecord]:
        with self._database.lock:
            rows = self._database.connection.execute(
                "SELECT * FROM scan_task ORDER BY created_at, task_id"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_active(self) -> ScanTaskRecord | None:
        """返回当前处于扫描或停止中的任务。"""

        placeholders = ", ".join("?" for _ in self._ACTIVE_STATUS_VALUES)
        with self._database.lock:
            row = self._database.connection.execute(
                f"""
                SELECT *
                FROM scan_task
                WHERE status IN ({placeholders})
                ORDER BY updated_at, task_id
                LIMIT 1
                """,
                self._ACTIVE_STATUS_VALUES,
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def claim_scan(
        self,
        task_id: str,
        *,
        scan_params_snapshot_json: str | None = None,
    ) -> ScanTaskRecord | None:
        """在立即事务中抢占扫描资格并将任务置为 `SCANNING`。

        返回 ``None`` 表示已有其他活动任务；目标任务不存在时抛出 ``KeyError``，
        目标任务不在可开始状态时抛出 ``ValueError``。
        """

        placeholders = ", ".join("?" for _ in self._ACTIVE_STATUS_VALUES)
        with self._database.transaction() as connection:
            task_row = connection.execute(
                "SELECT status FROM scan_task WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise KeyError(f"任务不存在：{task_id}")
            if task_row["status"] not in self._RESUMABLE_STATUS_VALUES:
                raise ValueError(
                    f"任务 {task_id} 当前状态 {task_row['status']} 不允许开始扫描"
                )

            active_row = connection.execute(
                f"""
                SELECT task_id
                FROM scan_task
                WHERE status IN ({placeholders})
                ORDER BY updated_at, task_id
                LIMIT 1
                """,
                self._ACTIVE_STATUS_VALUES,
            ).fetchone()
            if active_row is not None and active_row["task_id"] != task_id:
                return None

            timestamp = _utc_now()
            connection.execute(
                """
                UPDATE scan_task
                SET status = ?,
                    scan_params_snapshot_json = ?,
                    error_code = NULL,
                    error_message = NULL,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    TaskStatus.SCANNING.value,
                    scan_params_snapshot_json,
                    timestamp,
                    task_id,
                ),
            )
        record = self.get(task_id)
        if record is None:
            raise RuntimeError(f"任务 {task_id} 抢占后无法读取")
        return record

    def update_status(
        self,
        task_id: str,
        status: TaskStatus | str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ScanTaskRecord:
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE scan_task
                SET status = ?,
                    error_code = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    _status_value(status),
                    _optional_text(error_code),
                    _optional_text(error_message),
                    _utc_now(),
                    task_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"任务不存在：{task_id}")
        record = self.get(task_id)
        if record is None:
            raise RuntimeError(f"任务 {task_id} 更新后无法读取")
        return record

    def delete(self, task_id: str) -> bool:
        with self._database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM scan_task WHERE task_id = ?",
                (task_id,),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ScanTaskRecord:
        return ScanTaskRecord(
            task_id=row["task_id"],
            device_id=row["device_id"],
            status=TaskStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_page_sequence=row["last_page_sequence"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            device_snapshot_json=row["device_snapshot_json"],
            capability_snapshot_json=row["capability_snapshot_json"],
            scan_params_snapshot_json=row["scan_params_snapshot_json"],
        )
