"""Task 11 全部 HTTP 接口契约测试。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@dataclass
class SeededPage:
    task_id: str
    page_id: str
    original: Path
    thumbnail: Path


class FakeWorker:
    """只模拟主进程所需的 Worker 网关，不加载真实 TWAIN。"""

    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []
        self.raise_unknown = False
        self.ready = True
        self.pid = 4242
        self.devices = [
            {
                "deviceId": "device-1",
                "manufacturer": "KODAK",
                "productFamily": "i2000",
                "productName": "KODAK Scanner: i2000",
                "protocolMajor": 2,
                "protocolMinor": 5,
                "architecture": "x64",
                "online": True,
            }
        ]
        self.capabilities = [
            {
                "capabilityId": 100,
                "capabilityHex": "0x0064",
                "capabilityName": "CAP_FEEDERENABLED",
                "custom": False,
                "containerType": "TW_ONEVALUE",
                "itemType": "TWTY_BOOL",
                "operations": {
                    "get": True,
                    "set": False,
                    "getCurrent": False,
                    "getDefault": False,
                    "reset": False,
                },
                "currentValue": True,
                "defaultValue": False,
                "values": None,
                "source": {"manufacturer": "KODAK", "productName": "i2000"},
            },
            {
                "capabilityId": 32769,
                "capabilityHex": "0x8001",
                "capabilityName": None,
                "custom": True,
                "containerType": "TW_ENUMERATION",
                "itemType": "TWTY_UINT16",
                "operations": {
                    "get": True,
                    "set": True,
                    "getCurrent": True,
                    "getDefault": True,
                    "reset": False,
                },
                "currentValue": 1,
                "defaultValue": 0,
                "values": [0, 1, 2],
                "queryError": "TWAIN_CAPABILITY_QUERY_FAILED",
                "queryErrorMessage": "单项查询失败",
                "source": {"manufacturer": "KODAK", "productName": "i2000"},
            },
        ]

    @property
    def command_types(self) -> list[str]:
        return [item["type"] for item in self.commands]

    @property
    def last_command_type(self) -> str | None:
        return self.command_types[-1] if self.commands else None

    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def enumerate_devices(self) -> list[dict[str, Any]]:
        self.commands.append({"type": "enumerate_devices"})
        if self.raise_unknown:
            raise RuntimeError("D:\\private\\scanner\\failure.log")
        return [dict(item) for item in self.devices]

    def get_capabilities(self, device_id: str) -> list[dict[str, Any]]:
        self.commands.append({"type": "query_capabilities", "deviceId": device_id})
        return [dict(item) for item in self.capabilities]

    def resolve_capabilities(
        self,
        device_id: str,
        settings: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self.commands.append(
            {
                "type": "resolve_capabilities",
                "deviceId": device_id,
                "settings": dict(settings),
            }
        )
        return [dict(item) for item in self.capabilities]

    def start_scan(
        self,
        task_id: str,
        device_id: str,
        settings: dict[str, Any],
    ) -> None:
        self.commands.append(
            {
                "type": "start_scan",
                "taskId": task_id,
                "deviceId": device_id,
                "settings": dict(settings),
            }
        )

    def stop_scan(self, task_id: str) -> None:
        self.commands.append({"type": "stop_scan", "taskId": task_id})


@pytest.fixture
def fake_worker() -> FakeWorker:
    return FakeWorker()


@pytest.fixture
def context(tmp_path: Path, fake_worker: FakeWorker):
    from app.api.dependencies import ApplicationContext
    from app.config import Settings

    application = ApplicationContext(
        settings=Settings(data_root=tmp_path, allowed_origins=("http://localhost:3000",)),
        worker=fake_worker,
    )
    yield application
    application.close()


@pytest.fixture
def client(context):
    from app.main import create_app

    with TestClient(
        create_app(context=context),
        raise_server_exceptions=False,
    ) as test_client:
        yield test_client


@pytest.fixture
def seeded_page(context) -> SeededPage:
    task = context.task_service.create("task-1", "device-1")
    task_dir = context.tasks_root / task.task_id
    original = task_dir / "originals" / "page-000001.jpg"
    thumbnail = task_dir / "thumbnails" / "page-000001.jpg"
    original.parent.mkdir(parents=True, exist_ok=True)
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 480), (20, 90, 160)).save(original, format="JPEG")
    Image.new("RGB", (160, 120), (20, 90, 160)).save(thumbnail, format="JPEG")
    context.page_repository.create(
        "page-000001",
        task.task_id,
        1,
        "originals/page-000001.jpg",
        "thumbnails/page-000001.jpg",
        "a" * 64,
        original.stat().st_size,
        width=640,
        height=480,
    )
    return SeededPage(task.task_id, "page-000001", original, thumbnail)


def test_health_uses_standard_response(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["code"] == 200
    assert response.json()["data"]["status"] == "ok"
    assert response.json()["data"]["workerReady"] is True


def test_info_returns_runtime_contract_without_absolute_data_path(client):
    response = client.get("/api/v1/info")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["serviceName"] == "archive-scan-service"
    assert data["apiVersion"] == "v1"
    assert data["host"] == "127.0.0.1"
    assert data["port"] == 17653
    assert data["architecture"] == "x64"
    assert "dataRoot" not in data


def test_device_list_aggregates_worker_events(client, fake_worker):
    response = client.get("/api/v1/devices")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["devices"][0]["deviceId"] == "device-1"
    assert data["total"] == 1
    assert fake_worker.last_command_type == "enumerate_devices"


def test_capability_endpoint_keeps_standard_private_and_query_error(client):
    response = client.get("/api/v1/devices/device-1/capabilities")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["count"] == 2
    assert data["capabilities"][1]["custom"] is True
    assert data["capabilities"][1]["queryError"] == "TWAIN_CAPABILITY_QUERY_FAILED"


def test_capability_resolve_is_not_a_scan(client, fake_worker):
    response = client.post(
        "/api/v1/devices/device-1/capabilities/resolve",
        json={"settings": {"resolution": 300}},
    )

    assert response.status_code == 200
    assert response.json()["data"]["count"] == 2
    assert fake_worker.last_command_type == "resolve_capabilities"
    assert "start_scan" not in fake_worker.command_types


def test_task_create_list_detail_and_start(client, fake_worker):
    created = client.post(
        "/api/v1/tasks",
        json={"taskId": "task-1", "deviceId": "device-1"},
    )
    assert created.status_code == 200
    assert created.json()["data"]["status"] == "CREATED"

    listed = client.get("/api/v1/tasks")
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
    assert listed.json()["data"]["items"][0]["taskId"] == "task-1"

    started = client.post(
        "/api/v1/tasks/task-1/scan/start",
        json={"settings": {"feedMode": "adf_simplex", "resolution": 300}},
    )
    assert started.status_code == 200
    assert started.json()["data"]["status"] == "SCANNING"
    command = fake_worker.commands[-1]
    assert command["type"] == "start_scan"
    assert Path(command["settings"]["outputDir"]).is_relative_to(
        client.app.state.context.tasks_root / "task-1"
    )
    assert command["settings"]["pageId"].startswith("page-")

    detail = client.get("/api/v1/tasks/task-1")
    assert detail.status_code == 200
    assert detail.json()["data"]["pageCount"] == 0


def test_task_start_rejects_second_active_scan_with_stable_error(client):
    for task_id in ("task-1", "task-2"):
        assert client.post(
            "/api/v1/tasks",
            json={"taskId": task_id, "deviceId": "device-1"},
        ).status_code == 200
    assert client.post("/api/v1/tasks/task-1/scan/start", json={}).status_code == 200

    response = client.post("/api/v1/tasks/task-2/scan/start", json={})

    assert response.status_code == 409
    assert response.json()["data"]["errorCode"] == "SCANNER_BUSY"
    assert "D:\\" not in response.text


def test_task_stop_follows_lifecycle(client, fake_worker):
    client.post("/api/v1/tasks", json={"taskId": "task-1", "deviceId": "device-1"})
    client.post("/api/v1/tasks/task-1/scan/start", json={})

    stopping = client.post("/api/v1/tasks/task-1/scan/stop")
    assert stopping.status_code == 200
    assert stopping.json()["data"]["status"] == "STOPPING"
    assert fake_worker.last_command_type == "stop_scan"


def test_scan_complete_marks_active_task_completed(client):
    client.post("/api/v1/tasks", json={"taskId": "task-1", "deviceId": "device-1"})
    client.post("/api/v1/tasks/task-1/scan/start", json={})

    completed = client.post("/api/v1/tasks/task-1/scan/complete")

    assert completed.status_code == 200
    assert completed.json()["data"]["status"] == "COMPLETED"


def test_unknown_task_and_invalid_request_use_stable_error(client):
    missing = client.get("/api/v1/tasks/missing-task")
    assert missing.status_code == 404
    assert missing.json()["data"]["errorCode"] == "TASK_NOT_FOUND"

    invalid = client.post("/api/v1/tasks", json={"deviceId": ""})
    assert invalid.status_code == 400
    assert invalid.json()["data"]["errorCode"] == "INVALID_REQUEST"


def test_unknown_error_does_not_expose_absolute_path(client, fake_worker):
    fake_worker.raise_unknown = True

    response = client.get("/api/v1/devices")

    assert response.status_code == 500
    assert response.json() == {
        "code": 5000,
        "message": "服务内部错误",
        "data": {"errorCode": "INTERNAL_ERROR"},
    }
    assert "D:\\private" not in response.text


def test_page_list_detail_incremental_files_and_delete(client, seeded_page):
    pages = client.get(f"/api/v1/tasks/{seeded_page.task_id}/pages")
    assert pages.status_code == 200
    assert pages.json()["data"]["total"] == 1

    incremental = client.get(
        f"/api/v1/tasks/{seeded_page.task_id}/pages?afterSequence=1"
    )
    assert incremental.status_code == 200
    assert incremental.json()["data"]["items"] == []

    detail = client.get(
        f"/api/v1/tasks/{seeded_page.task_id}/pages/{seeded_page.page_id}"
    )
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["originalPath"] == "originals/page-000001.jpg"
    assert data["thumbnailUrl"].endswith("/thumbnail")

    original = client.get(data["originalUrl"])
    thumbnail = client.get(data["thumbnailUrl"])
    assert original.status_code == 200
    assert thumbnail.status_code == 200
    assert original.headers["content-type"] == "image/jpeg"
    assert thumbnail.headers["content-type"] == "image/jpeg"

    deleted = client.delete(
        f"/api/v1/tasks/{seeded_page.task_id}/pages/{seeded_page.page_id}"
    )
    assert deleted.status_code == 200
    assert not seeded_page.original.exists()
    assert not seeded_page.thumbnail.exists()
    assert client.get(data["originalUrl"]).status_code == 404


def test_page_and_task_delete_reject_active_task_and_remove_task(client, context):
    task = context.task_service.create("task-1", "device-1")
    task_dir = context.tasks_root / task.task_id
    (task_dir / "originals").mkdir(parents=True, exist_ok=True)
    (task_dir / "thumbnails").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 20), (1, 2, 3)).save(
        task_dir / "originals/page-1.jpg", format="JPEG"
    )
    Image.new("RGB", (10, 10), (1, 2, 3)).save(
        task_dir / "thumbnails/page-1.jpg", format="JPEG"
    )
    context.page_repository.create(
        "page-1",
        task.task_id,
        1,
        "originals/page-1.jpg",
        "thumbnails/page-1.jpg",
        "b" * 64,
        100,
    )
    client.post("/api/v1/tasks/task-1/scan/start", json={})

    blocked = client.delete("/api/v1/tasks/task-1")
    assert blocked.status_code == 409
    assert blocked.json()["data"]["errorCode"] == "TASK_STATE_INVALID"

    client.post("/api/v1/tasks/task-1/scan/complete")
    deleted = client.delete("/api/v1/tasks/task-1")
    assert deleted.status_code == 200
    assert not task_dir.exists()
    assert client.get("/api/v1/tasks/task-1").status_code == 404


def test_cors_uses_configured_origin(client):
    response = client.get(
        "/api/v1/health",
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
