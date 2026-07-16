"""SQLite 连接和事务边界。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
from types import TracebackType
from typing import Iterator

from .migrations import apply_migrations


@dataclass(slots=True)
class Database:
    """创建一个由主进程独占使用的 SQLite 连接。"""

    path: Path
    connection: sqlite3.Connection = field(init=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        apply_migrations(self.connection)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """以立即事务执行一次主进程写操作。"""

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def close(self) -> None:
        """关闭数据库连接。"""

        self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
