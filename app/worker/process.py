"""TWAIN 工作子进程入口和串行命令循环。"""

from __future__ import annotations

from collections.abc import Mapping
import os
from queue import Empty
import time
from typing import Any, Callable, Protocol

from app.scanner.twain_backend import TwainBackend, TwainBackendError, TwainDevice
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


class WorkerRuntime(Protocol):
    """工作进程内的 TWAIN 运行时边界。"""

    def enumerate_devices(self) -> list[TwainDevice]:
        """枚举当前工作进程可见的 TWAIN Data Source。"""

    def close(self) -> None:
        """释放工作进程内的 TWAIN 资源。"""


class NoopWorkerRuntime:
    """供隔离测试使用的最小运行时。"""

    def enumerate_devices(self) -> list[TwainDevice]:
        return []

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


def _handle_command(
    command: Message,
    event_queue: Any,
    *,
    active_scan: ScanCommand | None,
    runtime: WorkerRuntime,
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
        return command, False

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
            )
            return active_scan, False
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

    WorkerProcess(
        command_queue,
        event_queue,
        worker_id=worker_id,
        heartbeat_interval=heartbeat_interval,
        runtime_factory=TwainRuntime,
    ).run()
