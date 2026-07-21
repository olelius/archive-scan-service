"""Task 11 WebSocket 事件契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_api import FakeWorker


@pytest.fixture
def websocket_app(tmp_path: Path):
    from app.api.dependencies import ApplicationContext
    from app.config import Settings
    from app.main import create_app

    context = ApplicationContext(
        settings=Settings(data_root=tmp_path),
        worker=FakeWorker(),
    )
    application = create_app(context=context)
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client, context
    context.close()


def test_websocket_receives_task_event_and_applies_task_filter(websocket_app):
    client, context = websocket_app

    with client.websocket_connect("/api/v1/events?taskId=task-1") as websocket:
        context.event_hub.publish(
            {"event": "task_started", "taskId": "task-2", "data": {}}
        )
        context.event_hub.publish(
            {"event": "task_started", "taskId": "task-1", "data": {}}
        )
        event = websocket.receive_json()

    assert event["event"] == "task_started"
    assert event["taskId"] == "task-1"
    assert event["timestamp"]


def test_websocket_receives_service_event_without_task_id(websocket_app):
    client, context = websocket_app

    with client.websocket_connect("/api/v1/events?taskId=task-1") as websocket:
        context.event_hub.publish({"event": "worker_started", "data": {}})
        event = websocket.receive_json()

    assert event["event"] == "worker_started"
    assert event.get("taskId") is None
