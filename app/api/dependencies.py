"""FastAPI 依赖、主进程资源装配和 Worker IPC 网关。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
import threading
from typing import Any, Protocol
from uuid import uuid4

from fastapi import Request

from app.api.responses import (
    device_payload,
    extract_settings,
    require_identifier,
)
from app.config import Settings
from app.errors import ApiError
from app.models.enums import TaskStatus
from app.repositories.database import Database
from app.repositories.page_repository import PageRepository
from app.repositories.task_repository import TaskRepository
from app.scanner.pnp_status import DeviceStatusResolver, WindowsPnpStatusResolver
from app.scanner.protocol import CommandType
from app.services.event_hub import EventHub
from app.services.page_service import PageRegistrationError, PageService
from app.services.recovery_service import RecoveryService
from app.services.task_service import (
    TaskNotFoundError,
    TaskService,
    TaskServiceError,
)
from app.worker.messages import CommandMessage, EventMessage, ScanCommand
from app.worker.supervisor import (
    WorkerEvent,
    WorkerSupervisor,
    WorkerSupervisorError,
    WorkerUnavailableEvent,
    WorkerReadyEvent,
)


LOGGER = logging.getLogger("archive_scan_service")


class WorkerGatewayError(RuntimeError):
    """Worker 网关对 API 层暴露的稳定错误。"""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


class WorkerGatewayProtocol(Protocol):
    """测试替身和真实 Worker 网关共同实现的主进程边界。"""

    def start(self) -> None:
        """启动或连接 Worker。"""

    def shutdown(self) -> None:
        """关闭 Worker。"""

    def enumerate_devices(self) -> list[Mapping[str, Any]]:
        """枚举设备。"""

    def get_capabilities(self, device_id: str) -> list[Mapping[str, Any]]:
        """查询完整 Capability 快照。"""

    def resolve_capabilities(
        self,
        device_id: str,
        settings: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        """按固定配置重新查询 Capability。"""

    def start_scan(
        self,
        task_id: str,
        device_id: str,
        settings: Mapping[str, Any],
    ) -> None:
        """发送扫描命令。"""

    def stop_scan(self, task_id: str) -> None:
        """发送停止命令。"""


@dataclass
class _CommandWaiter:
    """一个 commandId 的事件聚合器。"""

    done: threading.Event = field(default_factory=threading.Event)
    events: list[EventMessage] = field(default_factory=list)


class WorkerGateway:
    """在主进程串行协调 Worker IPC、业务事件和同步 API 请求。"""

    def __init__(
        self,
        *,
        supervisor: WorkerSupervisor,
        task_service: TaskService,
        page_service: PageService,
        event_hub: EventHub,
    ) -> None:
        self._supervisor = supervisor
        self._tasks = task_service
        self._pages = page_service
        self._events = event_hub
        self._condition = threading.RLock()
        self._waiters: dict[str, _CommandWaiter] = {}
        self._devices: dict[str, dict[str, Any]] = {}
        self._open_device_id: str | None = None
        self._scan_metadata: dict[str, dict[str, str]] = {}
        self._pump_stop = threading.Event()
        self._pump_thread: threading.Thread | None = None
        self._started = False
        self._worker_ready = False
        self._worker_pid: int | None = None
        self._generation = 0

    def start(self) -> None:
        with self._condition:
            if self._started:
                return
            self._supervisor.start()
            self._pump_stop.clear()
            self._pump_thread = threading.Thread(
                target=self._pump_loop,
                name="ArchiveScanApiWorkerEvents",
                daemon=True,
            )
            self._pump_thread.start()
            self._started = True
        try:
            ready = self._supervisor.wait_until_ready(timeout=10.0)
        except Exception as exc:
            self.shutdown()
            raise WorkerGatewayError("WORKER_UNAVAILABLE", "扫描工作进程启动失败") from exc
        with self._condition:
            self._worker_ready = True
            self._worker_pid = ready.pid
            self._generation = ready.generation

    def shutdown(self) -> None:
        with self._condition:
            if not self._started:
                return
            self._started = False
            self._pump_stop.set()
            pump = self._pump_thread
            self._pump_thread = None
        self._supervisor.shutdown()
        if pump is not None and pump is not threading.current_thread():
            pump.join(timeout=5.0)
        with self._condition:
            self._worker_ready = False
            self._worker_pid = None
            self._open_device_id = None
            self._scan_metadata.clear()
            for waiter in self._waiters.values():
                waiter.done.set()
            self._waiters.clear()

    close = shutdown

    def status(self) -> dict[str, Any]:
        with self._condition:
            return {
                "ready": self._worker_ready,
                "pid": self._worker_pid,
                "generation": self._generation,
            }

    def enumerate_devices(self) -> list[Mapping[str, Any]]:
        command_id = self._new_command_id()
        events = self._send_and_wait(
            CommandMessage(command_id=command_id, message_type=CommandType.ENUMERATE_DEVICES.value)
        )
        devices = [
            dict(event.payload)
            for event in events
            if event.event_type == "device_listed"
        ]
        with self._condition:
            self._devices = {
                str(item["deviceId"]): device_payload(item)
                for item in devices
                if isinstance(item.get("deviceId"), str)
            }
        self._events.publish(
            {
                "event": "device_list_changed",
                "data": {"devices": list(self._devices.values()), "total": len(self._devices)},
            }
        )
        return list(self._devices.values())

    def get_capabilities(self, device_id: str) -> list[Mapping[str, Any]]:
        self._ensure_source(device_id)
        command_id = self._new_command_id()
        events = self._send_and_wait(
            CommandMessage(
                command_id=command_id,
                message_type=CommandType.QUERY_CAPABILITIES.value,
            )
        )
        for event in events:
            if event.event_type == "capabilities_queried":
                values = event.payload.get("capabilities", [])
                if isinstance(values, list):
                    return [dict(item) for item in values if isinstance(item, Mapping)]
        return []

    def resolve_capabilities(
        self,
        device_id: str,
        settings: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        self._ensure_source(device_id)
        command_id = self._new_command_id()
        events = self._send_and_wait(
            CommandMessage(
                command_id=command_id,
                message_type=CommandType.RESOLVE_CAPABILITIES.value,
                payload={"settings": dict(settings), "showUi": False},
            )
        )
        for event in events:
            if event.event_type == "capabilities_queried":
                values = event.payload.get("capabilities", [])
                if isinstance(values, list):
                    return [dict(item) for item in values if isinstance(item, Mapping)]
        return []

    def start_scan(
        self,
        task_id: str,
        device_id: str,
        settings: Mapping[str, Any],
    ) -> None:
        self._ensure_source(device_id)
        command_id = self._new_command_id()
        command = ScanCommand(
            command_id=command_id,
            task_id=task_id,
            device_id=device_id,
            settings=dict(settings),
        )
        with self._condition:
            self._scan_metadata[command_id] = {
                "taskId": task_id,
                "pageId": str(settings.get("pageId", "")),
            }
        try:
            self._supervisor.send(command)
        except WorkerSupervisorError as exc:
            with self._condition:
                self._scan_metadata.pop(command_id, None)
            message = str(exc)
            code = "SCANNER_BUSY" if "扫描" in message or "活动" in message else "WORKER_UNAVAILABLE"
            raise WorkerGatewayError(code, message) from exc

    def stop_scan(self, task_id: str) -> None:
        command = CommandMessage(
            command_id=self._new_command_id(),
            message_type=CommandType.STOP_SCAN.value,
            task_id=task_id,
        )
        try:
            self._supervisor.send(command)
        except WorkerSupervisorError as exc:
            raise WorkerGatewayError("WORKER_UNAVAILABLE", "扫描工作进程不可用") from exc

    def _ensure_source(self, device_id: str) -> None:
        with self._condition:
            device = self._devices.get(device_id)
        if device is None:
            self.enumerate_devices()
            with self._condition:
                device = self._devices.get(device_id)
        if device is None:
            raise WorkerGatewayError("TWAIN_SOURCE_NOT_FOUND", "TWAIN 设备不存在或当前不可用")
        with self._condition:
            if self._open_device_id == device_id:
                return
            previous = self._open_device_id
        if previous is not None:
            self._send_and_wait(
                CommandMessage(
                    command_id=self._new_command_id(),
                    message_type=CommandType.CLOSE_SOURCE.value,
                )
            )
        self._send_and_wait(
            CommandMessage(
                command_id=self._new_command_id(),
                message_type=CommandType.OPEN_SOURCE.value,
                payload={"productName": device["productName"], "showUi": False},
            )
        )
        with self._condition:
            self._open_device_id = device_id

    def _send_and_wait(self, command: CommandMessage, *, timeout: float = 30.0) -> list[EventMessage]:
        waiter = _CommandWaiter()
        with self._condition:
            self._waiters[command.command_id] = waiter
        try:
            self._supervisor.send(command)
        except WorkerSupervisorError as exc:
            with self._condition:
                self._waiters.pop(command.command_id, None)
            raise WorkerGatewayError("WORKER_UNAVAILABLE", "扫描工作进程不可用") from exc
        if not waiter.done.wait(timeout=timeout):
            with self._condition:
                self._waiters.pop(command.command_id, None)
            raise WorkerGatewayError("WORKER_UNAVAILABLE", "等待扫描工作进程响应超时")
        with self._condition:
            self._waiters.pop(command.command_id, None)
            events = list(waiter.events)
        failed = next((item for item in events if item.event_type == "command_failed"), None)
        if failed is not None:
            error_code = failed.payload.get("errorCode")
            error_message = failed.payload.get("errorMessage")
            raise WorkerGatewayError(
                error_code if isinstance(error_code, str) else "WORKER_UNAVAILABLE",
                error_message if isinstance(error_message, str) else "扫描工作进程命令失败",
            )
        return events

    def _new_command_id(self) -> str:
        return f"api-{uuid4().hex}"

    def _pump_loop(self) -> None:
        while not self._pump_stop.is_set():
            try:
                event = self._supervisor.wait_for_event(timeout=0.2)
            except Exception:
                event = None
            if event is not None:
                try:
                    self._handle_worker_event(event)
                except Exception:
                    LOGGER.exception("处理 Worker 事件失败")

    def _handle_worker_event(self, event: WorkerEvent) -> None:
        if isinstance(event, WorkerReadyEvent):
            with self._condition:
                self._worker_ready = True
                self._worker_pid = event.pid
                self._generation = event.generation
            self._events.publish(
                {
                    "event": "worker_restarted"
                    if event.generation > 1
                    else "worker_started",
                    "data": {"pid": event.pid, "generation": event.generation},
                }
            )
            return
        if isinstance(event, WorkerUnavailableEvent):
            with self._condition:
                self._worker_ready = False
                self._worker_pid = None
            if event.task_id:
                task = self._tasks.get(event.task_id)
                if task is not None and task.status in {TaskStatus.SCANNING, TaskStatus.STOPPING}:
                    try:
                        failed = self._tasks.fail_scan(
                            event.task_id,
                            "WORKER_UNAVAILABLE",
                            "扫描工作进程异常退出",
                        )
                        self._events.publish(
                            {
                                "event": "task_failed",
                                "taskId": failed.task_id,
                                "data": {
                                    "status": failed.status.value,
                                    "errorCode": failed.error_code,
                                },
                            }
                        )
                    except TaskServiceError:
                        LOGGER.exception("记录 Worker 异常任务失败状态失败")
            return
        if not isinstance(event, EventMessage):
            return
        if event.command_id is not None:
            with self._condition:
                waiter = self._waiters.get(event.command_id)
                if waiter is not None:
                    waiter.events.append(event)
                    if event.event_type in {"command_succeeded", "command_failed"}:
                        waiter.done.set()
        if event.event_type == "page_file_ready":
            self._handle_page_file_ready(event)
        elif event.event_type == "scan_started":
            self._handle_scan_started(event)
        elif event.event_type == "scan_stopped":
            self._handle_scan_stopped(event)
        elif event.event_type == "scan_completed":
            self._handle_scan_completed(event)
        elif event.event_type == "scan_failed":
            self._handle_scan_failed(event)

    def _handle_page_file_ready(self, event: EventMessage) -> None:
        try:
            self._pages.handle_page_file_ready(event, publish=self._events.publish)
        except PageRegistrationError as exc:
            self._fail_task_from_worker(event.task_id, exc.error_code, "页面登记失败")

    def _handle_scan_started(self, event: EventMessage) -> None:
        with self._condition:
            metadata = self._scan_metadata.get(event.command_id or "", {})
        page_id = metadata.get("pageId")
        if page_id:
            self._events.publish(
                {
                    "event": "page_started",
                    "taskId": event.task_id,
                    "data": {"pageId": page_id},
                }
            )

    def _handle_scan_stopped(self, event: EventMessage) -> None:
        if not event.task_id:
            return
        try:
            stopped = self._tasks.mark_stopped(event.task_id)
        except TaskServiceError:
            return
        self._events.publish(
            {
                "event": "task_stopped",
                "taskId": stopped.task_id,
                "data": {"status": stopped.status.value},
            }
        )

    def _handle_scan_completed(self, event: EventMessage) -> None:
        if not event.task_id:
            return
        try:
            completed = self._tasks.complete_scan(event.task_id)
        except TaskServiceError:
            return
        self._events.publish(
            {
                "event": "task_completed",
                "taskId": completed.task_id,
                "data": {
                    "status": completed.status.value,
                    "pageCount": event.payload.get("pageCount", 0),
                },
            }
        )
        with self._condition:
            self._scan_metadata.pop(event.command_id or "", None)

    def _handle_scan_failed(self, event: EventMessage) -> None:
        self._fail_task_from_worker(
            event.task_id,
            str(event.payload.get("errorCode", "SCAN_FAILED")),
            "扫描失败",
        )
        with self._condition:
            self._scan_metadata.pop(event.command_id or "", None)

    def _fail_task_from_worker(
        self,
        task_id: str | None,
        error_code: str,
        message: str,
    ) -> None:
        if not task_id:
            return
        task = self._tasks.get(task_id)
        if task is None or task.status not in {TaskStatus.SCANNING, TaskStatus.STOPPING}:
            return
        try:
            failed = self._tasks.fail_scan(task_id, error_code, message)
        except TaskServiceError:
            return
        self._events.publish(
            {
                "event": "task_failed",
                "taskId": failed.task_id,
                "data": {
                    "status": failed.status.value,
                    "errorCode": failed.error_code,
                    "errorMessage": failed.error_message,
                },
            }
        )


class ApplicationContext:
    """FastAPI lifespan 使用的主进程依赖容器。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        worker: WorkerGatewayProtocol | None = None,
        database: Database | None = None,
        device_status_resolver: DeviceStatusResolver | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.settings.ensure_directories()
        self.tasks_root = self.settings.tasks_dir.resolve()
        self.tasks_root.mkdir(parents=True, exist_ok=True)
        self.database = database or Database(self.settings.database_path)
        self.task_repository = TaskRepository(self.database)
        self.page_repository = PageRepository(self.database)
        self.task_service = TaskService(self.task_repository)
        self.page_service = PageService(
            task_repository=self.task_repository,
            page_repository=self.page_repository,
            tasks_root=self.tasks_root,
        )
        self.recovery_service = RecoveryService(self.task_repository)
        self.event_hub = EventHub()
        self.worker: WorkerGatewayProtocol = worker or WorkerGateway(
            supervisor=WorkerSupervisor(),
            task_service=self.task_service,
            page_service=self.page_service,
            event_hub=self.event_hub,
        )
        self._device_status_resolver = (
            device_status_resolver or WindowsPnpStatusResolver()
        )
        self._started = False
        self._closed = False
        self._devices: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        if self._started:
            return
        self.recovery_service.recover_on_startup()
        self.worker.start()
        self._started = True
        self.event_hub.publish(
            {
                "event": "service_started",
                "data": {"host": self.settings.host, "port": self.settings.port},
            }
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.worker.shutdown()
        finally:
            self.event_hub.close()
            self.database.close()

    def status(self) -> dict[str, Any]:
        status = getattr(self.worker, "status", None)
        if callable(status):
            value = status()
            return {
                "ready": bool(value.get("ready", False)),
                "pid": value.get("pid"),
                "generation": value.get("generation", 0),
            }
        return {
            "ready": bool(getattr(self.worker, "ready", False)),
            "pid": getattr(self.worker, "pid", None),
            "generation": getattr(self.worker, "generation", 0),
        }

    def list_devices(self) -> list[dict[str, Any]]:
        try:
            raw_devices = self.worker.enumerate_devices()
        except (ApiError, WorkerGatewayError):
            raise
        except Exception as exc:
            raise ApiError("INTERNAL_ERROR") from exc
        values = self._device_status_resolver.enrich_devices(raw_devices)
        self._devices = {
            item["deviceId"]: item for item in values if isinstance(item.get("deviceId"), str)
        }
        self.event_hub.publish(
            {
                "event": "device_list_changed",
                "data": {"devices": values, "total": len(values)},
            }
        )
        return values

    def get_capabilities(self, device_id: str) -> list[Mapping[str, Any]]:
        try:
            return list(self.worker.get_capabilities(device_id))
        except (ApiError, WorkerGatewayError):
            raise
        except Exception as exc:
            raise ApiError("INTERNAL_ERROR") from exc

    def resolve_capabilities(
        self,
        device_id: str,
        settings: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        try:
            return list(self.worker.resolve_capabilities(device_id, settings))
        except (ApiError, WorkerGatewayError):
            raise
        except Exception as exc:
            raise ApiError("INTERNAL_ERROR") from exc

    def create_task(self, body: Mapping[str, Any]) -> Any:
        device_id = require_identifier(body.get("deviceId"), "deviceId")
        task_id = body.get("taskId")
        if task_id is None:
            task_id = f"task-{uuid4().hex}"
        task_id = require_identifier(task_id, "taskId")
        device_snapshot = body.get("deviceSnapshot")
        if device_snapshot is None:
            device_snapshot = self._devices.get(device_id)
        for field_name in ("deviceSnapshot", "capabilitySnapshot", "scanParamsSnapshot"):
            value = body.get(field_name)
            if value is not None and not isinstance(value, Mapping):
                raise ValueError(f"{field_name} 必须是 JSON 对象")
        task = self.task_service.create(
            task_id,
            device_id,
            device_snapshot=device_snapshot,
            capability_snapshot=body.get("capabilitySnapshot"),
            scan_params_snapshot=body.get("scanParamsSnapshot"),
        )
        self.event_hub.publish(
            {
                "event": "task_created",
                "taskId": task.task_id,
                "data": {"deviceId": task.device_id, "status": task.status.value},
            }
        )
        return task

    def start_scan(self, task_id: str, body: Mapping[str, Any] | None) -> Any:
        task = self._require_task(task_id)
        settings = extract_settings(body)
        stored_settings = dict(settings)
        started = self.task_service.start_scan(task_id, stored_settings)
        task_dir = (self.tasks_root / task_id).resolve()
        output_dir = (task_dir / "originals").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        page_id = f"page-{self.page_repository.next_sequence(task_id):06d}"
        worker_settings = {
            key: value for key, value in settings.items() if key not in {"outputDir", "pageId"}
        }
        worker_settings.update({"outputDir": str(output_dir), "pageId": page_id})
        try:
            self.worker.start_scan(task_id, task.device_id, worker_settings)
        except Exception as exc:
            try:
                failed = self.task_service.fail_scan(
                    task_id,
                    getattr(exc, "error_code", "WORKER_UNAVAILABLE"),
                    "扫描工作进程不可用",
                )
                self.event_hub.publish(
                    {
                        "event": "task_failed",
                        "taskId": failed.task_id,
                        "data": {
                            "status": failed.status.value,
                            "errorCode": failed.error_code,
                        },
                    }
                )
            except TaskServiceError:
                pass
            if isinstance(exc, (ApiError, WorkerGatewayError)):
                raise
            raise ApiError("INTERNAL_ERROR") from exc
        self.event_hub.publish(
            {
                "event": "task_started",
                "taskId": task_id,
                "data": {"status": started.status.value, "pageId": page_id},
            }
        )
        self.event_hub.publish(
            {
                "event": "page_started",
                "taskId": task_id,
                "data": {"pageId": page_id},
            }
        )
        return started

    def stop_scan(self, task_id: str) -> Any:
        stopping = self.task_service.stop_scan(task_id)
        try:
            self.worker.stop_scan(task_id)
        except Exception as exc:
            self.task_service.fail_scan(task_id, "WORKER_UNAVAILABLE", "扫描工作进程不可用")
            if isinstance(exc, (ApiError, WorkerGatewayError)):
                raise
            raise ApiError("INTERNAL_ERROR") from exc
        self.event_hub.publish(
            {
                "event": "task_stopping",
                "taskId": task_id,
                "data": {"status": stopping.status.value},
            }
        )
        return stopping

    def complete_scan(self, task_id: str) -> Any:
        completed = self.task_service.complete_scan(task_id)
        self.event_hub.publish(
            {
                "event": "task_completed",
                "taskId": task_id,
                "data": {"status": completed.status.value},
            }
        )
        return completed

    def delete_task(self, task_id: str) -> None:
        self.page_service.delete_task(task_id)
        self.event_hub.publish(
            {"event": "task_deleted", "taskId": task_id, "data": {"taskId": task_id}}
        )

    def _require_task(self, task_id: str) -> Any:
        task_id = require_identifier(task_id, "taskId")
        task = self.task_service.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task


def get_context(request: Request) -> ApplicationContext:
    return request.app.state.context


def raise_api_error(exc: BaseException) -> None:
    if isinstance(exc, ApiError):
        raise exc
    error_code = getattr(exc, "error_code", None)
    if isinstance(error_code, str) and error_code:
        raise ApiError(error_code) from exc
    if isinstance(exc, ValueError):
        raise ApiError("INVALID_REQUEST") from exc
    raise exc


__all__ = [
    "ApplicationContext",
    "WorkerGateway",
    "WorkerGatewayError",
    "WorkerGatewayProtocol",
    "get_context",
]
