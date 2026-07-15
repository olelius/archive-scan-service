import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _flush_handlers(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.flush()


def test_configure_logging_creates_utf8_rotating_log_files(tmp_path: Path):
    from app.config import Settings
    from app.logging_config import configure_logging

    settings = Settings(data_root=tmp_path)
    logger = configure_logging(settings)

    assert logger.name == "archive_scan_service"
    assert (settings.logs_dir / "service.log").is_file()
    assert (settings.logs_dir / "twain-worker.log").is_file()
    assert (settings.logs_dir / "error.log").is_file()
    assert any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers)


def test_logs_keep_identifiers_but_redact_paths_binary_and_tracebacks(tmp_path: Path):
    from app.config import Settings
    from app.logging_config import configure_logging

    settings = Settings(data_root=tmp_path)
    logger = configure_logging(settings)
    sensitive_path = str(Path.home() / "Documents" / "原图.jpg")

    logger.info(
        "任务 %s 页面 %s 路径 %s 内容 %r 错误码 %s",
        "task-1",
        "page-1",
        sensitive_path,
        b"\xff\xd8JFIF\x00\x01",
        "SCAN_FAILED",
    )
    try:
        raise RuntimeError("内部异常")
    except RuntimeError:
        logger.exception("任务 %s 失败", "task-1")
    _flush_handlers(logger)

    content = (settings.logs_dir / "service.log").read_text(encoding="utf-8")

    assert "task-1" in content
    assert "page-1" in content
    assert "SCAN_FAILED" in content
    assert sensitive_path not in content
    assert "JFIF" not in content
    assert "Traceback" not in content
