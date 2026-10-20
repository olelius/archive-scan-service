"""TWAIN 工作子进程入口和串行命令循环。"""

from __future__ import annotations

from collections.abc import Generator, Mapping
import logging
import os
from queue import Empty
import time
from typing import Any, Callable, Protocol

from app.config import Settings
from app.logging_config import configure_logging
from app.models.schemas import CapabilitySchema
from app.scanner.diagnostics import describe_failure, failure_message, safe_diagnostics
from app.scanner.file_transfer import FileTransferResult
from app.scanner.twain_backend import (
    TwainBackend,
    TwainBackendError,
    TwainDevice,
    TwainScanStopped,
)
from app.scanner.protocol import CommandType
from app.worker.messages import (
    CommandMessage,
    EventMessage,
    Message,
    MessageError,
    ScanCommand,
    decode_message,
    encode_message,
)


LOGGER = logging.getLogger("archive_scan_service.worker")


class WorkerRuntime(Protocol):
    """工作进程内的 TWAIN 运行时边界。"""

    def enumerate_devices(self) -> list[TwainDevice]:
        """枚举当前工作进程可见的 TWAIN Data Source。"""

    def open_source(
        self,
        product_name: str,
        *,
        show_ui: bool = False,
    ) -> dict[str, Any]:
        """无界面打开一个 TWAIN Data Source。"""

    def query_capabilities(self) -> list[CapabilitySchema]:
        """查询当前打开 Data Source 的全部 Capability。"""

    def resolve_capabilities(
        self,
        settings: Mapping[str, Any],
    ) -> list[CapabilitySchema]:
        """应用固定配置并重新查询 Capability，不开始扫描。"""

    def scan_pages(
        self,
        device_id: str,
        settings: Mapping[str, Any],
        *,
        should_stop: Callable[[], bool],
    ) -> Generator[FileTransferResult, None, None]:
        """执行一轮扫描并逐面返回原图。"""

    def close_source(self) -> None:
        """关闭当前打开的 TWAIN Data Source。"""

    def close(self) -> None:
        """释放工作进程内的 TWAIN 资源。"""


class NoopWorkerRuntime:
    """供隔离测试使用的最小运行时。"""

    def enumerate_devices(self) -> list[TwainDevice]:
        return []

    def open_source(
        self,
        product_name: str,
        *,
        show_ui: bool = False,
    ) -> dict[str, Any]:
        raise TwainBackendError(
            "TWAIN_SOURCE_NOT_FOUND", "测试运行时没有 TWAIN Data Source"
        )

    def query_capabilities(self) -> list[CapabilitySchema]:
        raise TwainBackendError(
            "TWAIN_SOURCE_NOT_OPEN",
            "测试运行时没有打开 TWAIN Data Source",
        )

    def resolve_capabilities(
        self, settings: Mapping[str, Any]
    ) -> list[CapabilitySchema]:
        raise TwainBackendError(
            "TWAIN_SOURCE_NOT_OPEN",
            "测试运行时没有打开 TWAIN Data Source",
        )

    def scan_pages(
        self,
        device_id: str,
        settings: Mapping[str, Any],
        *,
        should_stop: Callable[[], bool],
    ) -> Generator[FileTransferResult, None, None]:
        raise TwainBackendError(
            "TWAIN_SOURCE_NOT_OPEN",
            "测试运行时没有打开 TWAIN Data Source",
        )

    def close_source(self) -> None:
        return None

    def close(self) -> None:
        return None


class TwainRuntime:
    """只在工作子进程内构造 TWAIN 后端的运行时边界。"""

    def __init__(self) -> None:
        self._backend: TwainBackend | None = TwainBackend()

    def enumerate_devices(self) -> list[TwainDevice]:
        if self._backend is None:
            raise RuntimeError("TWAIN 后端已经关闭")
        return self._backend.enumerate_devices()

    def open_source(
        self,
        product_name: str,
        *,
        show_ui: bool = False,
    ) -> dict[str, Any]:
        if self._backend is None:
            raise RuntimeError("TWAIN 后端已经关闭")
        return self._backend.open_source(product_name, show_ui=show_ui)

    def query_capabilities(self) -> list[CapabilitySchema]:
        if self._backend is None:
            raise RuntimeError("TWAIN 后端已经关闭")
        return self._backend.query_capabilities()

    def resolve_capabilities(
        self,
        settings: Mapping[str, Any],
    ) -> list[CapabilitySchema]:
        if self._backend is None:
            raise RuntimeError("TWAIN 后端已经关闭")
        return self._backend.resolve_capabilities(settings)

    def scan_pages(
        self,
        device_id: str,
        settings: Mapping[str, Any],
        *,
        should_stop: Callable[[], bool],
    ) -> Generator[FileTransferResult, None, None]:
        if self._backend is None:
            raise RuntimeError("TWAIN 后端已经关闭")
        output_dir = settings.get("outputDir")
        if not isinstance(output_dir, str) or not output_dir:
            raise TwainBackendError("SCAN_FAILED", "扫描必须提供 outputDir")
        page_id = settings.get("pageId", "page-1")
        if not isinstance(page_id, str) or not page_id:
            raise TwainBackendError("SCAN_FAILED", "扫描 pageId 必须是非空字符串")
        return self._backend.scan_pages(
            output_dir,
            page_id=page_id,
            settings=settings,
            should_stop=should_stop,
        )

    def close_source(self) -> None:
        if self._backend is not None:
            self._backend.close_source()

    def close(self) -> None:
        backend = self._backend
        self._backend = None
        if backend is not None:
            backend.close()


def _emit(event_queue: Any, event: EventMessage) -> None:
    event_queue.put(encode_message(event))


def _command_failed(
    event_queue: Any,
    command: Message,
    *,
    error_code: str,
    error_message: str,
    diagnostics: Mapping[str, Any] | None = None,
) -> None:
    if isinstance(command, ScanCommand):
        command_id = command.command_id
        task_id = command.task_id
    elif isinstance(command, CommandMessage):
        command_id = command.command_id
        task_id = command.task_id
    else:
        return
    _emit(
        event_queue,
        EventMessage(
            event_type="command_failed",
            command_id=command_id,
            task_id=task_id,
            payload={
                "errorCode": error_code,
                "errorMessage": error_message,
                "diagnostics": safe_diagnostics(diagnostics),
            },
        ),
    )


def _command_succeeded(
    event_queue: Any,
    command: CommandMessage,
    *,
    payload: Mapping[str, Any] | None = None,
) -> None:
    _emit(
        event_queue,
        EventMessage(
            event_type="command_succeeded",
            command_id=command.command_id,
            task_id=command.task_id,
            payload=dict(payload or {}),
        ),
    )


class _ScanControl:
    """在 TWAIN 线程内处理扫描中的停止、关闭和心跳。"""

    def __init__(
        self,
        command_queue: Any,
        event_queue: Any,
        scan: ScanCommand,
        *,
        heartbeat_interval: float,
    ) -> None:
        self._commands = command_queue
        self._events = event_queue
        self._scan = scan
        self._heartbeat_interval = heartbeat_interval
        self._next_heartbeat = time.monotonic() + heartbeat_interval
        self.stopped = False
        self.shutdown_command: CommandMessage | None = None

    def poll(self) -> bool:
        if time.monotonic() >= self._next_heartbeat:
            _emit(
                self._events,
                EventMessage(
                    event_type="worker_heartbeat",
                    payload={"pid": os.getpid()},
                ),
            )
            self._next_heartbeat = time.monotonic() + self._heartbeat_interval
        while not self.stopped:
            try:
                raw = self._commands.get_nowait()
            except Empty:
                break
            try:
                command = decode_message(raw)
            except MessageError:
                continue
            if not isinstance(command, (CommandMessage, ScanCommand)):
                continue
            if isinstance(command, CommandMessage):
                if command.message_type == CommandType.SHUTDOWN.value:
                    self.shutdown_command = command
                    self.stopped = True
                    break
                if command.message_type == CommandType.STOP_SCAN.value:
                    if command.task_id == self._scan.task_id:
                        self.stopped = True
                        break
                    _command_failed(
                        self._events,
                        command,
                        error_code="TASK_STATE_INVALID",
                        error_message="停止请求与活动任务不匹配",
                    )
                    continue
            _command_failed(
                self._events,
                command,
                error_code="SCANNER_BUSY",
                error_message="工作进程正在扫描",
            )
        return self.stopped

    def finish(self) -> bool:
        """TWAIN 清理完成后才确认关闭命令。"""

        if self.shutdown_command is None:
            return False
        _command_succeeded(self._events, self.shutdown_command)
        return True


def _close_failed_source(runtime: WorkerRuntime) -> bool:
    """失败后不复用旧 Data Source；关闭失败时退出以重建 Worker。"""

    close = getattr(runtime, "close_source", None)
    if close is None:
        return False
    try:
        close()
        LOGGER.info("任务数据源已关闭，下次请求重新打开")
        return False
    except Exception as exc:
        LOGGER.error(
            "失败后数据源关闭失败，重建工作进程：%s",
            describe_failure(exc, "close_source"),
        )
        return True


def _handle_command(
    command: Message,
    event_queue: Any,
    *,
    active_scan: ScanCommand | None,
    runtime: WorkerRuntime,
    scan_control: _ScanControl | None = None,
) -> tuple[ScanCommand | None, bool]:
    """处理一条命令，返回新的扫描状态和是否退出。"""

    if isinstance(command, ScanCommand):
        if active_scan is not None:
            _command_failed(
                event_queue,
                command,
                error_code="SCANNER_BUSY",
                error_message="工作进程已有活动扫描任务",
            )
            return active_scan, False
        _emit(
            event_queue,
            EventMessage(
                event_type="scan_started",
                command_id=command.command_id,
                task_id=command.task_id,
                payload={"pid": os.getpid()},
            ),
        )
        # 没有输出目录的生命周期命令只验证“扫描中”状态，不能启动文件传输。
        if not isinstance(command.settings.get("outputDir"), str):
            return command, False
        page_count = 0
        result: FileTransferResult | None = None
        try:
            pages = runtime.scan_pages(
                command.device_id,
                command.settings,
                should_stop=scan_control.poll if scan_control else lambda: False,
            )
            try:
                for result in pages:
                    page_count += 1
                    page_payload: dict[str, Any] = {
                        "pageId": result.original_path.stem,
                        "path": str(result.original_path),
                        "size": result.size,
                        "transferReturnCode": result.transfer_return_code,
                        "pendingCount": result.pending_count,
                    }
                    if result.configuration_results:
                        page_payload["configurationResults"] = [
                            dict(item) for item in result.configuration_results
                        ]
                    _emit(
                        event_queue,
                        EventMessage(
                            event_type="page_file_ready",
                            command_id=command.command_id,
                            task_id=command.task_id,
                            payload=page_payload,
                        ),
                    )
            finally:
                pages.close()
            if scan_control and scan_control.poll():
                raise TwainScanStopped()
        except TwainScanStopped:
            _emit(
                event_queue,
                EventMessage(
                    event_type="scan_stopped",
                    command_id=command.command_id,
                    task_id=command.task_id,
                    payload={"pageCount": page_count},
                ),
            )
            restart = _close_failed_source(runtime)
            return None, (scan_control.finish() if scan_control else False) or restart
        except TwainBackendError as exc:
            _emit(
                event_queue,
                EventMessage(
                    event_type="scan_failed",
                    command_id=command.command_id,
                    task_id=command.task_id,
                    payload={
                        "errorCode": exc.error_code,
                        "errorMessage": str(exc),
                        "diagnostics": exc.diagnostics,
                    },
                ),
            )
            restart = _close_failed_source(runtime)
            return None, (scan_control.finish() if scan_control else False) or restart
        except Exception as exc:
            detail = describe_failure(exc, "scan")
            LOGGER.error("工作进程扫描失败：诊断=%s", detail)
            _emit(
                event_queue,
                EventMessage(
                    event_type="scan_failed",
                    command_id=command.command_id,
                    task_id=command.task_id,
                    payload={
                        "errorCode": "SCAN_FAILED",
                        "errorMessage": failure_message("SCAN_FAILED", detail),
                        "diagnostics": detail,
                    },
                ),
            )
            restart = _close_failed_source(runtime)
            return None, (scan_control.finish() if scan_control else False) or restart

        completion_payload: dict[str, Any] = {
            "pageCount": page_count,
            "pendingCount": 0,
        }
        if result is not None:
            completion_payload["transferReturnCode"] = result.transfer_return_code
            if result.configuration_results:
                completion_payload["configurationResults"] = [
                    dict(item) for item in result.configuration_results
                ]
        _emit(
            event_queue,
            EventMessage(
                event_type="scan_completed",
                command_id=command.command_id,
                task_id=command.task_id,
                payload=completion_payload,
            ),
        )
        return None, False

    if command.message_type == CommandType.ENUMERATE_DEVICES.value:
        if active_scan is not None:
            _command_failed(
                event_queue,
                command,
                error_code="SCANNER_BUSY",
                error_message="扫描期间不能枚举设备",
            )
            return active_scan, False
        try:
            devices = runtime.enumerate_devices()
        except TwainBackendError as exc:
            _command_failed(
                event_queue,
                command,
                error_code=exc.error_code,
                error_message=str(exc),
                diagnostics=exc.diagnostics,
            )
            return active_scan, _close_failed_source(runtime)
        except Exception:
            _command_failed(
                event_queue,
                command,
                error_code="TWAIN_SOURCE_ENUMERATION_FAILED",
                error_message="TWAIN Data Source 枚举失败",
            )
            return active_scan, False

        for device in devices:
            _emit(
                event_queue,
                EventMessage(
                    event_type="device_listed",
                    command_id=command.command_id,
                    payload=device.to_payload(),
                ),
            )
        _command_succeeded(
            event_queue,
            command,
            payload={"count": len(devices)},
        )
        return active_scan, False

    if command.message_type in {
        CommandType.OPEN_SOURCE.value,
        CommandType.QUERY_CAPABILITIES.value,
        CommandType.RESOLVE_CAPABILITIES.value,
        CommandType.CLOSE_SOURCE.value,
    }:
        if active_scan is not None:
            _command_failed(
                event_queue,
                command,
                error_code="SCANNER_BUSY",
                error_message="扫描期间不能操作 Data Source 或查询 Capability",
            )
            return active_scan, False
        try:
            if command.message_type == CommandType.OPEN_SOURCE.value:
                product_name = command.payload.get("productName")
                show_ui = command.payload.get("showUi", False)
                if not isinstance(product_name, str) or not product_name:
                    raise TwainBackendError(
                        "TWAIN_SOURCE_NOT_FOUND",
                        "Data Source 产品名不能为空",
                    )
                if show_ui is not False:
                    raise TwainBackendError(
                        "TWAIN_UI_FORBIDDEN",
                        "Capability 冒烟探测禁止打开厂商界面",
                    )
                source_payload = runtime.open_source(
                    product_name,
                    show_ui=False,
                )
                _command_succeeded(
                    event_queue,
                    command,
                    payload=source_payload,
                )
                return active_scan, False

            if command.message_type == CommandType.QUERY_CAPABILITIES.value:
                capabilities = runtime.query_capabilities()
                payload = {
                    "count": len(capabilities),
                    "capabilities": [item.to_payload() for item in capabilities],
                }
                _emit(
                    event_queue,
                    EventMessage(
                        event_type="capabilities_queried",
                        command_id=command.command_id,
                        payload=payload,
                    ),
                )
                _command_succeeded(
                    event_queue,
                    command,
                    payload={"count": len(capabilities)},
                )
                return active_scan, False

            if command.message_type == CommandType.RESOLVE_CAPABILITIES.value:
                settings = command.payload.get("settings", {})
                if not isinstance(settings, Mapping):
                    raise TwainBackendError(
                        "TWAIN_CAPABILITY_SET_FAILED",
                        "Capability resolve settings 必须是 JSON 对象",
                    )
                capabilities = runtime.resolve_capabilities(settings)
                _emit(
                    event_queue,
                    EventMessage(
                        event_type="capabilities_queried",
                        command_id=command.command_id,
                        payload={
                            "count": len(capabilities),
                            "capabilities": [
                                item.to_payload() for item in capabilities
                            ],
                        },
                    ),
                )
                _command_succeeded(
                    event_queue,
                    command,
                    payload={"count": len(capabilities)},
                )
                return active_scan, False

            runtime.close_source()
            _command_succeeded(event_queue, command)
            return active_scan, False
        except TwainBackendError as exc:
            _command_failed(
                event_queue,
                command,
                error_code=exc.error_code,
                error_message=str(exc),
                diagnostics=exc.diagnostics,
            )
            return active_scan, _close_failed_source(runtime)
        except Exception:
            _command_failed(
                event_queue,
                command,
                error_code="TWAIN_CAPABILITY_QUERY_FAILED",
                error_message="TWAIN Data Source Capability 操作失败",
            )
            return active_scan, False

    if command.message_type == CommandType.SHUTDOWN.value:
        _command_succeeded(event_queue, command)
        return active_scan, True

    if command.message_type == CommandType.STOP_SCAN.value:
        if active_scan is None:
            _command_failed(
                event_queue,
                command,
                error_code="TASK_STATE_INVALID",
                error_message="当前没有活动扫描任务",
            )
            return None, False
        _emit(
            event_queue,
            EventMessage(
                event_type="scan_stopped",
                command_id=command.command_id,
                task_id=active_scan.task_id,
                payload={},
            ),
        )
        return None, False

    _command_succeeded(event_queue, command)
    return active_scan, False


class WorkerProcess:
    """在子进程中运行的长期命令循环。"""

    def __init__(
        self,
        command_queue: Any,
        event_queue: Any,
        *,
        worker_id: str,
        heartbeat_interval: float = 0.5,
        runtime_factory: Callable[[], WorkerRuntime] = TwainRuntime,
    ) -> None:
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval 必须大于 0")
        self._command_queue = command_queue
        self._event_queue = event_queue
        self._worker_id = worker_id
        self._heartbeat_interval = heartbeat_interval
        self._runtime_factory = runtime_factory

    def run(self) -> None:
        """初始化运行时并串行处理来自主进程的命令。"""

        runtime = self._runtime_factory()
        active_scan: ScanCommand | None = None
        next_heartbeat = time.monotonic() + self._heartbeat_interval
        try:
            _emit(
                self._event_queue,
                EventMessage(
                    event_type="worker_ready",
                    payload={"pid": os.getpid(), "workerId": self._worker_id},
                ),
            )
            while True:
                timeout = max(0.05, next_heartbeat - time.monotonic())
                try:
                    raw_message = self._command_queue.get(timeout=timeout)
                except Empty:
                    _emit(
                        self._event_queue,
                        EventMessage(
                            event_type="worker_heartbeat",
                            payload={"pid": os.getpid()},
                        ),
                    )
                    next_heartbeat = time.monotonic() + self._heartbeat_interval
                    continue

                try:
                    command = decode_message(raw_message)
                except MessageError:
                    # 无法关联 commandId 的非法消息只能丢弃，不能伪造命令失败事件。
                    next_heartbeat = time.monotonic() + self._heartbeat_interval
                    continue
                if not isinstance(command, (ScanCommand, CommandMessage)):
                    next_heartbeat = time.monotonic() + self._heartbeat_interval
                    continue

                active_scan, should_exit = _handle_command(
                    command,
                    self._event_queue,
                    active_scan=active_scan,
                    runtime=runtime,
                    scan_control=_ScanControl(
                        self._command_queue,
                        self._event_queue,
                        command,
                        heartbeat_interval=self._heartbeat_interval,
                    )
                    if isinstance(command, ScanCommand)
                    else None,
                )
                next_heartbeat = time.monotonic() + self._heartbeat_interval
                if should_exit:
                    return
        finally:
            runtime.close()


def worker_process_entry(
    command_queue: Any,
    event_queue: Any,
    worker_id: str,
    heartbeat_interval: float,
) -> None:
    """multiprocessing.spawn 使用的顶层子进程入口。"""

    configure_logging(Settings())
    WorkerProcess(
        command_queue,
        event_queue,
        worker_id=worker_id,
        heartbeat_interval=heartbeat_interval,
        runtime_factory=TwainRuntime,
    ).run()
