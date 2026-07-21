"""主进程页面接收、摘要、缩略图和 SQLite 登记服务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil

from PIL import Image

from app.models.records import ScanPageRecord
from app.models.enums import TaskStatus
from app.repositories.page_repository import PageRepository
from app.repositories.task_repository import TaskRepository
from app.worker.messages import EventMessage

from .thumbnail_service import ThumbnailService


PageCompletedPublisher = Callable[[dict[str, object]], None]


class PageRegistrationError(RuntimeError):
    """页面登记失败，并携带可供上层记录的稳定错误码。"""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


class PageService:
    """把 Worker 的 `page_file_ready` 转换为有效页面记录。"""

    def __init__(
        self,
        *,
        task_repository: TaskRepository,
        page_repository: PageRepository,
        tasks_root: str | Path,
        thumbnail_service: ThumbnailService | None = None,
    ) -> None:
        self._tasks = task_repository
        self._pages = page_repository
        self._tasks_root = Path(tasks_root).resolve()
        self._thumbnails = thumbnail_service or ThumbnailService()

    def handle_page_file_ready(
        self,
        event: EventMessage,
        *,
        publish: PageCompletedPublisher | None = None,
    ) -> ScanPageRecord:
        """登记一条 Worker 已经完成传输的页面文件。"""

        if event.event_type != "page_file_ready":
            raise PageRegistrationError(
                "INVALID_PAGE_EVENT",
                "页面服务只接受 page_file_ready 事件",
            )
        if not event.task_id:
            raise PageRegistrationError(
                "INVALID_PAGE_EVENT",
                "page_file_ready 缺少 taskId",
            )
        path = event.payload.get("path")
        if not isinstance(path, str) or not path:
            raise PageRegistrationError(
                "INVALID_PAGE_EVENT",
                "page_file_ready 缺少原图路径",
            )
        page_id = event.payload.get("pageId")
        if page_id is not None and not isinstance(page_id, str):
            raise PageRegistrationError(
                "INVALID_PAGE_EVENT",
                "pageId 必须是字符串",
            )
        return self.register_page(
            event.task_id,
            path,
            page_id=page_id,
            publish=publish,
        )

    def register_page(
        self,
        task_id: str,
        original_path: str | Path,
        *,
        page_id: str | None = None,
        publish: PageCompletedPublisher | None = None,
    ) -> ScanPageRecord:
        """校验、登记一面原图并在成功后发布业务页面事件。"""

        task = self._tasks.get(task_id)
        if task is None:
            raise PageRegistrationError("TASK_NOT_FOUND", f"任务不存在：{task_id}")

        thumbnail_path: Path | None = None
        registered = False
        try:
            task_dir, source_path, derived_page_id = self._resolve_original(
                task_id,
                original_path,
            )
            resolved_page_id = self._resolve_page_id(page_id, derived_page_id)
            width, height = self._read_image_size(source_path)
            file_size = source_path.stat().st_size
            digest = self._sha256(source_path)
            sequence = self._pages.next_sequence(task_id)

            thumbnail_path = task_dir / "thumbnails" / f"{resolved_page_id}.jpg"
            try:
                self._thumbnails.create(source_path, thumbnail_path)
            except Exception as exc:
                raise PageRegistrationError(
                    "THUMBNAIL_FAILED",
                    "缩略图生成失败",
                ) from exc
            record = self._pages.create(
                resolved_page_id,
                task_id,
                sequence,
                self._relative_to_task(task_dir, source_path),
                self._relative_to_task(task_dir, thumbnail_path),
                digest,
                file_size,
                width=width,
                height=height,
            )
            registered = True
        except PageRegistrationError as exc:
            self._remove_unregistered_thumbnail(thumbnail_path, registered)
            self._record_error(task_id, exc.error_code, str(exc))
            raise
        except Exception as exc:
            self._remove_unregistered_thumbnail(thumbnail_path, registered)
            error = PageRegistrationError("PAGE_PERSIST_FAILED", "页面记录写入失败")
            self._record_error(task_id, error.error_code, str(error))
            raise error from exc

        if publish is not None:
            publish(self._page_completed_event(record))
        return record

    def resolve_page_file(self, page: ScanPageRecord, *, kind: str) -> Path:
        """把数据库相对路径安全解析为任务目录内的现有普通文件。"""

        if kind == "original":
            relative_path = page.original_path
        elif kind == "thumbnail":
            relative_path = page.thumbnail_path
        else:
            raise ValueError("页面文件类型只能是 original 或 thumbnail")
        task_dir = (self._tasks_root / page.task_id).resolve()
        try:
            candidate = (task_dir / relative_path).resolve(strict=True)
            candidate.relative_to(task_dir)
        except (FileNotFoundError, OSError) as exc:
            raise PageRegistrationError("FILE_NOT_FOUND", "页面文件不存在") from exc
        except ValueError as exc:
            raise PageRegistrationError("PAGE_PATH_INVALID", "页面文件路径无效") from exc
        if not candidate.is_file():
            raise PageRegistrationError("FILE_NOT_FOUND", "页面文件不存在")
        return candidate

    def delete_page(self, task_id: str, page_id: str) -> None:
        """删除一个已登记页面的原图、缩略图和数据库记录。"""

        task = self._tasks.get(task_id)
        if task is None:
            raise PageRegistrationError("TASK_NOT_FOUND", "扫描任务不存在")
        if task.status in {TaskStatus.SCANNING, TaskStatus.STOPPING}:
            raise PageRegistrationError("TASK_STATE_INVALID", "扫描期间不能删除页面")
        page = self._pages.get(task_id, page_id)
        if page is None:
            raise PageRegistrationError("PAGE_NOT_FOUND", "扫描页面不存在")
        original = self.resolve_page_file(page, kind="original")
        thumbnail = self.resolve_page_file(page, kind="thumbnail")
        try:
            original.unlink()
            thumbnail.unlink()
        except OSError as exc:
            raise PageRegistrationError("FILE_DELETE_FAILED", "页面文件删除失败") from exc
        if not self._pages.delete(task_id, page_id):
            raise PageRegistrationError("FILE_DELETE_FAILED", "页面记录删除失败")

    def delete_task(self, task_id: str) -> None:
        """删除任务目录和全部页面记录，只有明确调用时才执行。"""

        task = self._tasks.get(task_id)
        if task is None:
            raise PageRegistrationError("TASK_NOT_FOUND", "扫描任务不存在")
        if task.status in {TaskStatus.SCANNING, TaskStatus.STOPPING}:
            raise PageRegistrationError("TASK_STATE_INVALID", "扫描期间不能删除任务")
        pages = self._pages.list_by_task(task_id)
        for page in pages:
            self.resolve_page_file(page, kind="original")
            self.resolve_page_file(page, kind="thumbnail")
        task_dir = (self._tasks_root / task_id).resolve()
        try:
            task_dir.relative_to(self._tasks_root)
            if task_dir.exists():
                shutil.rmtree(task_dir)
        except (OSError, ValueError) as exc:
            raise PageRegistrationError("FILE_DELETE_FAILED", "任务文件删除失败") from exc
        if not self._tasks.delete(task_id):
            raise PageRegistrationError("FILE_DELETE_FAILED", "任务记录删除失败")

    def _resolve_original(
        self,
        task_id: str,
        original_path: str | Path,
    ) -> tuple[Path, Path, str]:
        self._validate_segment(task_id, "task_id")
        path = Path(original_path)
        if not path.is_absolute():
            raise PageRegistrationError("PAGE_PATH_INVALID", "原图路径必须是绝对路径")

        task_dir = (self._tasks_root / task_id).resolve()
        originals_dir = (task_dir / "originals").resolve()
        try:
            candidate = path.resolve(strict=True)
            relative = candidate.relative_to(originals_dir)
        except (FileNotFoundError, OSError) as exc:
            raise PageRegistrationError("PAGE_FILE_MISSING", "原图文件不存在") from exc
        except ValueError as exc:
            raise PageRegistrationError(
                "PAGE_PATH_INVALID",
                "原图路径必须位于任务原图目录内",
            ) from exc

        if len(relative.parts) != 1:
            raise PageRegistrationError(
                "PAGE_PATH_INVALID",
                "原图路径必须位于任务原图目录内",
            )
        if candidate.suffix.lower() != ".jpg" or not candidate.is_file():
            raise PageRegistrationError("PAGE_FILE_INVALID", "原图必须是 JPEG 文件")
        page_id = candidate.stem
        self._validate_segment(page_id, "page_id")
        return task_dir, candidate, page_id

    @staticmethod
    def _resolve_page_id(explicit: str | None, derived: str) -> str:
        if explicit is None:
            return derived
        PageService._validate_segment(explicit, "page_id")
        if explicit != derived:
            raise PageRegistrationError(
                "PAGE_ID_MISMATCH",
                "pageId 与原图文件名不一致",
            )
        return explicit

    @staticmethod
    def _validate_segment(value: str, field_name: str) -> None:
        path = Path(value)
        if (
            not value
            or path.name != value
            or path.anchor
            or value in {".", ".."}
        ):
            raise PageRegistrationError(
                "INVALID_IDENTIFIER",
                f"{field_name} 必须是单段非空标识",
            )

    @staticmethod
    def _read_image_size(path: Path) -> tuple[int, int]:
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                return image.size
        except Exception as exc:
            raise PageRegistrationError(
                "PAGE_FILE_INVALID",
                "原图不是有效 JPEG 文件",
            ) from exc

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise PageRegistrationError("PAGE_FILE_READ_FAILED", "原图读取失败") from exc
        return digest.hexdigest()

    @staticmethod
    def _relative_to_task(task_dir: Path, path: Path) -> str:
        try:
            return path.resolve().relative_to(task_dir.resolve()).as_posix()
        except ValueError as exc:
            raise PageRegistrationError(
                "PAGE_PATH_INVALID",
                "页面文件必须位于任务目录内",
            ) from exc

    def _record_error(self, task_id: str, error_code: str, message: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        try:
            self._tasks.update_status(
                task_id,
                task.status,
                error_code=error_code,
                error_message=message,
            )
        except Exception:
            pass

    @staticmethod
    def _remove_unregistered_thumbnail(
        path: Path | None,
        registered: bool,
    ) -> None:
        if path is None or registered:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _page_completed_event(record: ScanPageRecord) -> dict[str, object]:
        return {
            "event": "page_completed",
            "taskId": record.task_id,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "data": {
                "pageId": record.page_id,
                "sequence": record.sequence,
                "originalPath": record.original_path,
                "thumbnailPath": record.thumbnail_path,
                "sha256": record.sha256,
                "fileSize": record.file_size,
                "width": record.width,
                "height": record.height,
            },
        }


__all__ = ["PageRegistrationError", "PageService"]
