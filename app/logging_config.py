"""配置中文日志、大小轮转和敏感内容脱敏。"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from logging.handlers import RotatingFileHandler
from os import PathLike
from pathlib import Path
import re
from typing import Any

from app.config import Settings


APP_LOGGER_NAME = "archive_scan_service"
WORKER_LOGGER_NAME = f"{APP_LOGGER_NAME}.worker"

_WINDOWS_PATH_PATTERN = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\"'<>|]+"
)


def _redact_string(value: str) -> str:
    return _WINDOWS_PATH_PATTERN.sub("<本机路径>", value)


def _redact_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<二进制数据，长度={len(value)}>"
    if isinstance(value, PathLike):
        return "<本机路径>"
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, Mapping):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, set):
        return {_redact_value(item) for item in value}
    return value


class RedactingFilter(logging.Filter):
    """移除路径、二进制内容和异常堆栈，保留业务标识和错误码。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_value(record.msg)
        if isinstance(record.args, Mapping):
            record.args = {
                key: _redact_value(value) for key, value in record.args.items()
            }
        elif record.args:
            record.args = tuple(_redact_value(value) for value in record.args)

        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def _resolve_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = getattr(logging, level.upper(), None)
    if not isinstance(resolved, int):
        raise ValueError(f"无效日志级别：{level}")
    return resolved


def _close_handlers(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()


def _new_handler(
    path: Path,
    *,
    level: int,
    max_bytes: int,
    backup_count: int,
) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.addFilter(RedactingFilter())
    return handler


def configure_logging(settings: Settings) -> logging.Logger:
    """配置服务日志和 TWAIN 工作进程日志，返回服务日志记录器。"""

    settings.ensure_directories()
    level = _resolve_level(settings.log_level)
    logger = logging.getLogger(APP_LOGGER_NAME)
    worker_logger = logging.getLogger(WORKER_LOGGER_NAME)
    _close_handlers(logger)
    _close_handlers(worker_logger)

    for configured_logger in (logger, worker_logger):
        configured_logger.setLevel(level)
        configured_logger.propagate = False

    common_options = {
        "max_bytes": settings.max_log_file_size_bytes,
        "backup_count": settings.log_backup_count,
    }
    service_handler = _new_handler(
        settings.logs_dir / "service.log",
        level=level,
        **common_options,
    )
    worker_handler = _new_handler(
        settings.logs_dir / "twain-worker.log",
        level=level,
        **common_options,
    )
    service_error_handler = _new_handler(
        settings.logs_dir / "error.log",
        level=logging.ERROR,
        **common_options,
    )
    worker_error_handler = _new_handler(
        settings.logs_dir / "error.log",
        level=logging.ERROR,
        **common_options,
    )
    logger.addHandler(service_handler)
    logger.addHandler(service_error_handler)
    worker_logger.addHandler(worker_handler)
    worker_logger.addHandler(worker_error_handler)
    return logger
