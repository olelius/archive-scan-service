"""TWAIN 工作子进程的主进程客户端和生命周期监督器。"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
import multiprocessing
from multiprocessing.context import BaseContext
from queue import Empty
import threading
import time
from typing import Any
from uuid import uuid4

from app.worker.client import WorkerClient, WorkerClientError
from app.worker.messages import (
    CommandMessage,
    EventMessage,
    JsonValue,
    ScanCommand,
)
from app.worker.process import worker_process_entry


class WorkerSupervisorError(RuntimeError):
    """工作进程监督器错误。"""


class WorkerSupervisorClosedError(WorkerSupervisorError):
    """监督器已经关闭。"""


@dataclass(frozen=True, slots=True)
class WorkerReadyEvent:
    """主进程确认某一代工作进程已经进入命令循环。"""

    pid: int
    generation: int
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    event_type: str = field(default="worker_ready", init=False)


@dataclass(frozen=True, slots=True)
class WorkerUnavailableEvent:
    """监督器发现工作进程退出时发布的主进程内部事件。"""

    pid: int
    exit_code: int | None
    command_id: str | None = None
    task_id: str | None = None
    generation: int = 0
    error_code: str = "WORKER_UNAVAILABLE"
    event_type: str = field(default="worker_unavailable", init=False)


WorkerEvent = EventMessage | WorkerReadyEvent | WorkerUnavailableEvent


@dataclass(frozen=True, slots=True)
class _ActiveScan:
    command_id: str
    task_id: str


class WorkerSupervisor:
    """在主进程中启动、监控、重启和回收长期工作子进程。"""

    def __init__(
        self,
        *,
        context: BaseContext | None = None,
        heartbeat_interval: float = 0.5,
        monitor_poll_interval: float = 0.05,
        shutdown_timeout: float = 5.0,
    ) -> None:
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval 必须大于 0")
        if monitor_poll_interval <= 0:
            raise ValueError("monitor_poll_interval 必须大于 0")
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout 必须大于 0")

        self._context = context or multiprocessing.get_context("spawn")
        self._heartbeat_interval = heartbeat_interval
        self._monitor_poll_interval = monitor_poll_interval
        self._shutdown_timeout = shutdown_timeout
        self._condition = threading.Condition()
        self._monitor_stop = threading.Event()
        self._shutdown_requested = False
        self._process: Any | None = None
        self._client: WorkerClient | None = None
        self._generation = 0
        self._active_scan: _ActiveScan | None = None
        self._events: deque[WorkerEvent] = deque(maxlen=256)
        self._ready_events: deque[WorkerReadyEvent] = deque()
        self._monitor_thread: threading.Thread | None = None

    def start(self) -> Any:
        """启动工作进程；已运行时返回当前进程对象。"""

        with self._condition:
            self._ensure_not_closed()
            if self._process is not None and self._process.is_alive():
                return self._process
            process = self._start_worker_locked()
            self._ensure_monitor_locked()
            return process

    def send(self, command: ScanCommand | CommandMessage) -> None:
        """通过当前客户端发送命令，并登记活动扫描上下文。"""

        with self._condition:
            self._ensure_not_closed()
            process = self._process
            client = self._client
            if process is None or client is None or not process.is_alive():
                raise WorkerSupervisorError("工作进程尚未运行")
            if isinstance(command, ScanCommand):
                if self._active_scan is not None:
                    raise WorkerSupervisorError("已有活动扫描任务")
                self._active_scan = _ActiveScan(
                    command_id=command.command_id,
                    task_id=command.task_id,
                )
            elif self._active_scan is not None and command.message_type not in {
                "stop_scan",
                "shutdown",
            }:
                raise WorkerSupervisorError("扫描期间只允许停止扫描或关闭工作进程")
            try:
                client.send(command)
            except Exception:
                if isinstance(command, ScanCommand):
                    self._active_scan = None
                raise

    def wait_until_ready(self, timeout: float) -> WorkerReadyEvent:
        """等待下一代工作进程的 ready 事件。"""

        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._ready_events:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待工作进程 ready 超时")
                self._condition.wait(timeout=remaining)
            return self._ready_events.popleft()

    def wait_for_event(self, timeout: float | None = None) -> WorkerEvent | None:
        """取出监督器发布的下一个事件，超时返回 None。"""

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._events:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)
            return self._events.popleft()

    def force_terminate_for_test(self) -> None:
        """仅供集成测试使用，模拟工作进程异常退出。"""

        with self._condition:
            process = self._process
        if process is None or not process.is_alive():
            raise WorkerSupervisorError("没有可终止的工作进程")
        process.terminate()
        process.join(timeout=self._shutdown_timeout)

    def shutdown(self) -> None:
        """优先发送 shutdown，超时后强制回收工作进程。"""

        with self._condition:
            if self._shutdown_requested:
                monitor = self._monitor_thread
                process = self._process
                client = self._client
            else:
                self._shutdown_requested = True
                monitor = self._monitor_thread
                process = self._process
                client = self._client

        if process is not None and process.is_alive() and client is not None:
            try:
                client.send(
                    CommandMessage(
                        command_id=f"shutdown-{uuid4().hex}",
                        message_type="shutdown",
                    )
                )
            except (WorkerClientError, OSError):
                pass
            process.join(timeout=self._shutdown_timeout)
            if process.is_alive():
                process.terminate()
                process.join(timeout=self._shutdown_timeout)

        self._monitor_stop.set()
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=self._shutdown_timeout)

        with self._condition:
            client = self._client or client
            self._process = None
            self._client = None
            self._active_scan = None
            self._condition.notify_all()
        if client is not None:
            client.close()

    @property
    def process(self) -> Any | None:
        """返回当前工作进程对象，供状态展示和测试读取。"""

        with self._condition:
            return self._process

    def _start_worker_locked(self) -> Any:
        self._generation += 1
        worker_id = f"worker-{self._generation}"
        command_queue = self._context.Queue()
        event_queue = self._context.Queue()
        process = self._context.Process(
            target=worker_process_entry,
            args=(
                command_queue,
                event_queue,
                worker_id,
                self._heartbeat_interval,
            ),
            name=f"ArchiveScanWorker-{self._generation}",
        )
        client = WorkerClient(command_queue, event_queue)
        try:
            process.start()
        except BaseException:
            client.close()
            raise
        self._process = process
        self._client = client
        return process

    def _ensure_monitor_locked(self) -> None:
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="ArchiveScanWorkerSupervisor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.is_set():
            with self._condition:
                client = self._client
                process = self._process
            if client is not None:
                try:
                    event = client.receive(timeout=self._monitor_poll_interval)
                except Empty:
                    event = None
                except (WorkerClientError, EOFError, OSError):
                    event = None
                if event is not None:
                    self._handle_worker_event(event)

            with self._condition:
                process = self._process
                if process is not None and not process.is_alive():
                    self._handle_process_exit_locked(process)
                if self._shutdown_requested and self._process is None:
                    return

    def _handle_worker_event(self, event: EventMessage) -> None:
        with self._condition:
            if event.event_type == "worker_ready":
                pid_value = event.payload.get("pid")
                pid = (
                    pid_value
                    if isinstance(pid_value, int) and not isinstance(pid_value, bool)
                    else self._process.pid if self._process is not None else -1
                )
                ready = WorkerReadyEvent(
                    pid=pid,
                    generation=self._generation,
                    payload=dict(event.payload),
                )
                self._ready_events.append(ready)
                self._events.append(ready)
            else:
                if event.event_type in {
                    "scan_stopped",
                    "scan_completed",
                    "scan_failed",
                }:
                    self._active_scan = None
                self._events.append(event)
            self._condition.notify_all()

    def _handle_process_exit_locked(self, process: Any) -> None:
        if process is not self._process:
            return
        process.join(timeout=0)
        client = self._client
        active_scan = self._active_scan
        unavailable = WorkerUnavailableEvent(
            pid=process.pid,
            exit_code=process.exitcode,
            command_id=active_scan.command_id if active_scan else None,
            task_id=active_scan.task_id if active_scan else None,
            generation=self._generation,
        )
        self._process = None
        self._client = None
        self._active_scan = None
        if client is not None:
            client.close()
        if not self._shutdown_requested:
            self._events.append(unavailable)
        self._condition.notify_all()

        if self._shutdown_requested:
            return
        self._start_worker_locked()

    def _ensure_not_closed(self) -> None:
        if self._shutdown_requested:
            raise WorkerSupervisorClosedError("工作进程监督器已经关闭")
