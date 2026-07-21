"""事件总线的跨线程发布和订阅边界测试。"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_subscriber_receives_published_event_with_timestamp():
    from app.services.event_hub import EventHub

    hub = EventHub(queue_size=2)
    async with hub.subscribe() as subscription:
        hub.publish({"event": "task_started", "taskId": "task-1", "data": {}})
        event = await asyncio.wait_for(subscription.receive(), timeout=1)

    assert event["event"] == "task_started"
    assert event["taskId"] == "task-1"
    assert event["timestamp"]


@pytest.mark.asyncio
async def test_full_queue_drops_oldest_event_without_blocking():
    from app.services.event_hub import EventHub

    hub = EventHub(queue_size=1)
    async with hub.subscribe() as subscription:
        hub.publish({"event": "first", "data": {}})
        hub.publish({"event": "second", "data": {}})
        event = await asyncio.wait_for(subscription.receive(), timeout=1)

    assert event["event"] == "second"
