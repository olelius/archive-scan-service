"""跨线程安全的 WebSocket 事件广播。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import threading
from typing import Any


class EventHubClosedError(RuntimeError):
    """事件总线或订阅已经关闭。"""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class EventSubscription:
    """绑定到一个 asyncio 事件循环的有界订阅队列。"""

    def __init__(
        self,
        hub: EventHub,
        loop: asyncio.AbstractEventLoop,
        *,
        task_id: str | None,
        queue_size: int,
    ) -> None:
        self._hub = hub
        self._loop = loop
        self.task_id = task_id
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self._closed = False
        self._lock = threading.Lock()

    def matches(self, event: Mapping[str, Any]) -> bool:
        return self.task_id is None or event.get("taskId") in {None, self.task_id}

    def enqueue(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            if self._closed:
                return
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._put_nowait, dict(event))

    def _put_nowait(self, event: dict[str, Any]) -> None:
        with self._lock:
            if self._closed:
                return
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            return

    async def receive(self) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise EventHubClosedError("事件订阅已经关闭")
        return await self._queue.get()

    async def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._hub.unsubscribe(self)

    async def __aenter__(self) -> EventSubscription:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


class EventHub:
    """接收主进程线程事件并广播给全部 WebSocket 订阅者。"""

    def __init__(self, *, queue_size: int = 128) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size 必须大于 0")
        self._queue_size = queue_size
        self._lock = threading.RLock()
        self._subscriptions: set[EventSubscription] = set()
        self._closed = False

    def subscribe(self, *, task_id: str | None = None) -> EventSubscription:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("事件订阅必须在 asyncio 事件循环中创建") from exc
        with self._lock:
            if self._closed:
                raise EventHubClosedError("事件总线已经关闭")
            subscription = EventSubscription(
                self,
                loop,
                task_id=task_id,
                queue_size=self._queue_size,
            )
            self._subscriptions.add(subscription)
            return subscription

    def unsubscribe(self, subscription: EventSubscription) -> None:
        with self._lock:
            self._subscriptions.discard(subscription)

    def publish(self, event: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(event, Mapping):
            raise TypeError("事件必须是 JSON 对象")
        event_name = event.get("event")
        if not isinstance(event_name, str) or not event_name:
            raise ValueError("事件必须包含非空 event")
        data = event.get("data", {})
        if not isinstance(data, Mapping):
            raise ValueError("事件 data 必须是 JSON 对象")
        normalized = deepcopy(dict(event))
        normalized["event"] = event_name
        normalized.setdefault("taskId", None)
        normalized.setdefault("timestamp", _timestamp())
        normalized["data"] = deepcopy(dict(data))
        with self._lock:
            if self._closed:
                return normalized
            subscriptions = tuple(self._subscriptions)
        for subscription in subscriptions:
            if subscription.matches(normalized):
                subscription.enqueue(normalized)
        return normalized

    def close(self) -> None:
        with self._lock:
            self._closed = True
            subscriptions = tuple(self._subscriptions)
            self._subscriptions.clear()
        for subscription in subscriptions:
            with subscription._lock:
                subscription._closed = True


__all__ = ["EventHub", "EventHubClosedError", "EventSubscription"]
