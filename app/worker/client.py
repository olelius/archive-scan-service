"""主进程使用的工作进程 IPC 客户端。"""

from __future__ import annotations

from threading import Lock
from typing import Any

from app.worker.messages import (
    CommandMessage,
    EventMessage,
    Message,
    ScanCommand,
    decode_message,
    encode_message,
)


class WorkerClientError(RuntimeError):
    """工作进程客户端错误。"""


class WorkerClientClosedError(WorkerClientError):
    """客户端已经关闭。"""


class WorkerClient:
    """只在主进程中把消息模型编码后放入工作进程队列。"""

    def __init__(self, command_queue: Any, event_queue: Any) -> None:
        self._command_queue = command_queue
        self._event_queue = event_queue
        self._lock = Lock()
        self._closed = False

    def send(self, message: ScanCommand | CommandMessage) -> None:
        """发送一条命令，队列中只保留 JSON 字符串。"""

        if not isinstance(message, (ScanCommand, CommandMessage)):
            raise TypeError("WorkerClient.send 只接受命令消息")
        raw_message = encode_message(message)
        with self._lock:
            self._ensure_open()
            self._command_queue.put(raw_message)

    def receive(self, timeout: float | None = None) -> Message:
        """接收并校验一条工作进程事件。"""

        with self._lock:
            self._ensure_open()
        raw_message = self._event_queue.get(timeout=timeout)
        message = decode_message(raw_message)
        if not isinstance(message, EventMessage):
            raise WorkerClientError("工作进程事件队列收到命令消息")
        return message

    def close(self) -> None:
        """关闭队列句柄，不影响主进程其它资源。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
        for queue in (self._command_queue, self._event_queue):
            queue.close()
            queue.cancel_join_thread()

    def _ensure_open(self) -> None:
        if self._closed:
            raise WorkerClientClosedError("工作进程客户端已经关闭")
