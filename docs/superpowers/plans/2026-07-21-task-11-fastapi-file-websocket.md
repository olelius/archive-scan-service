# Task 11 FastAPI、文件接口和 WebSocket Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `127.0.0.1:17653` 提供完整的 `/api/v1` HTTP 接口、JPEG 文件接口和 `/api/v1/events` WebSocket，并把 Worker、SQLite、页面登记和任务状态机接入同一个主进程应用上下文。

**Architecture:** FastAPI lifespan 装配主进程资源；Worker 网关在主进程中以唯一事件泵聚合 IPC 事件、等待命令结果并驱动任务/页面服务；EventHub 把业务事件广播给 WebSocket。所有路径从数据库相对路径解析到任务根目录，所有非文件接口返回 `{code,message,data}`。

**Tech Stack:** Python 3.12.13、FastAPI、Starlette `FileResponse`/`CORSMiddleware`、Pydantic v2、Uvicorn、SQLite、pytest、pytest-asyncio、httpx、Pillow。

---

## 文件结构

- Create: `app/errors.py` — 稳定 API 异常、错误码和统一异常处理。
- Create: `app/api/__init__.py` — API 包导出。
- Create: `app/api/responses.py` — 成功/失败响应、领域记录序列化和请求字段校验。
- Create: `app/api/dependencies.py` — ApplicationContext、WorkerGateway、FastAPI 依赖和生命周期。
- Create: `app/api/health.py` — 健康检查和运行信息。
- Create: `app/api/devices.py` — 设备枚举、Capability 查询和 resolve。
- Create: `app/api/tasks.py` — 任务创建、列表、详情、扫描开始/停止/完成和任务删除。
- Create: `app/api/pages.py` — 页面查询、增量查询、文件响应、页面删除。
- Create: `app/api/events.py` — WebSocket 订阅、过滤和事件发送。
- Create: `app/services/event_hub.py` — 跨线程到 asyncio 的有界事件订阅广播。
- Create: `app/main.py` — `create_app()`、路由和 lifespan 装配。
- Create: `run.py` — 固定回环地址启动 Uvicorn。
- Modify: `app/config.py` — 增加配置的 CORS 来源读取，不改变固定 host/port。
- Modify: `app/services/page_service.py` — 页面/任务文件安全解析和显式删除编排。
- Modify: `app/worker/process.py` — 完成 `resolve_capabilities` IPC 命令。
- Modify: `app/worker/supervisor.py` — 暴露 Worker generation/活动任务只读状态供健康信息和网关使用。
- Modify: `app/scanner/twain_backend.py` — 增加只查询 Capability 的 resolve 运行时入口。
- Modify: `pyproject.toml` — 确保 `app.api` 包被打包收集。
- Create: `tests/integration/test_api.py` — 全部 HTTP 路由、错误、文件和 CORS 测试。
- Create: `tests/integration/test_websocket.py` — WebSocket 广播、过滤和释放测试。
- Create: `tests/unit/test_event_hub.py` — EventHub 跨线程/有界队列测试。

### Task 1：统一响应、错误和 EventHub

**Files:**
- Create: `app/errors.py`
- Create: `app/api/__init__.py`
- Create: `app/api/responses.py`
- Create: `app/services/event_hub.py`
- Test: `tests/unit/test_event_hub.py`
- Test: `tests/integration/test_api.py`

- [ ] **Step 1: 写失败测试**

```python
def test_health_uses_standard_response(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["code"] == 200
    assert response.json()["data"]["status"] == "ok"


def test_unknown_error_does_not_expose_absolute_path(client, broken_context):
    broken_context.raise_unknown = True
    response = client.get("/api/v1/devices")
    assert response.status_code == 500
    assert response.json() == {
        "code": 5000,
        "message": "服务内部错误",
        "data": {"errorCode": "INTERNAL_ERROR"},
    }
```

- [ ] **Step 2: 运行失败测试**

Run: `D:\archive-scan-service\.venv\python.exe -m pytest tests/integration/test_api.py::test_health_uses_standard_response tests/integration/test_api.py::test_unknown_error_does_not_expose_absolute_path -v`

Expected: FAIL，因为应用和统一响应尚未创建。

- [ ] **Step 3: 实现最小响应和事件订阅**

实现 `success(data, message="操作成功")` 返回 `JSONResponse`，实现 `ApiError(http_status, code, error_code, message)`，并在 FastAPI 异常处理器中把请求校验、404、领域异常和未知异常统一成稳定 JSON。`EventHub.publish()` 只复制 JSON 对象；每个订阅使用 `asyncio.Queue(maxsize=128)`，队列满时丢弃最旧事件。

- [ ] **Step 4: 运行专项测试**

Run: `D:\archive-scan-service\.venv\python.exe -m pytest tests/integration/test_api.py::test_health_uses_standard_response tests/integration/test_api.py::test_unknown_error_does_not_expose_absolute_path tests/unit/test_event_hub.py -v`

Expected: PASS。

- [ ] **Step 5: 提交边界组件**

```powershell
git add app/errors.py app/api/__init__.py app/api/responses.py app/services/event_hub.py tests/unit/test_event_hub.py tests/integration/test_api.py
git commit -m "feat: 增加HTTP统一响应与事件总线"
```

### Task 2：ApplicationContext、Worker 网关和应用生命周期

**Files:**
- Create: `app/api/dependencies.py`
- Create: `app/main.py`
- Modify: `app/config.py`
- Modify: `app/worker/process.py`
- Modify: `app/worker/supervisor.py`
- Modify: `app/scanner/twain_backend.py`
- Test: `tests/integration/test_api.py`

- [ ] **Step 1: 写失败测试**

```python
def test_device_list_aggregates_worker_events(client, fake_worker):
    response = client.get("/api/v1/devices")
    assert response.status_code == 200
    assert response.json()["data"]["devices"][0]["deviceId"] == "device-1"
    assert fake_worker.commands[-1]["type"] == "enumerate_devices"


def test_capability_resolve_is_not_a_scan(client, fake_worker):
    response = client.post(
        "/api/v1/devices/device-1/capabilities/resolve",
        json={"settings": {"resolution": 300}},
    )
    assert response.status_code == 200
    assert fake_worker.last_command_type == "resolve_capabilities"
    assert "start_scan" not in fake_worker.command_types
```

- [ ] **Step 2: 运行失败测试**

Run: `D:\archive-scan-service\.venv\python.exe -m pytest tests/integration/test_api.py::test_device_list_aggregates_worker_events tests/integration/test_api.py::test_capability_resolve_is_not_a_scan -v`

Expected: FAIL，因为 ApplicationContext、WorkerGateway 和 resolve 处理尚不存在。

- [ ] **Step 3: 实现上下文与事件泵**

装配数据库、仓储、任务/页面/恢复服务、`WorkerSupervisor` 和 `EventHub`。WorkerGateway 注册 commandId 等待器后发送命令，由唯一后台线程消费 `wait_for_event()`；设备列表等待 `device_listed` + `command_succeeded`，Capability 等待 `capabilities_queried` + `command_succeeded`。事件泵按设计文档映射任务生命周期和 `page_file_ready`，未知 Worker 事件只记录诊断，不把绝对路径推给 API。

为 `resolve_capabilities` 增加 WorkerRuntime/`TwainBackend` 的只查询入口，仍然强制 `showUi=False`，不触发扫描；Worker 运行时命令失败返回已有稳定错误码。ApplicationContext 提供 fake worker 注入点，真实 lifespan 默认启动和关闭 Worker，测试上下文不启动真实 TWAIN。

- [ ] **Step 4: 运行生命周期专项测试**

Run: `D:\archive-scan-service\.venv\python.exe -m pytest tests/integration/test_api.py::test_device_list_aggregates_worker_events tests/integration/test_api.py::test_capability_resolve_is_not_a_scan tests/integration/test_worker_lifecycle.py -v`

Expected: PASS。

- [ ] **Step 5: 提交运行时装配**

```powershell
git add app/api/dependencies.py app/main.py app/config.py app/worker/process.py app/worker/supervisor.py app/scanner/twain_backend.py tests/integration/test_api.py
git commit -m "feat: 接入FastAPI主进程与Worker事件泵"
```

### Task 3：服务、设备、Capability 和任务接口

**Files:**
- Create: `app/api/health.py`
- Create: `app/api/devices.py`
- Create: `app/api/tasks.py`
- Modify: `app/api/responses.py`
- Test: `tests/integration/test_api.py`

- [ ] **Step 1: 写失败测试**

逐项写入 `/health`、`/info`、`/devices`、`/capabilities`、`/tasks`、任务详情、扫描开始/停止/完成、未知任务、状态错误、`SCANNER_BUSY` 和非法请求测试；任务列表同时断言 `items`、`tasks`、`total`，Capability 断言自定义 Capability 和 `queryError` 均保留。

- [ ] **Step 2: 运行失败测试**

Run: `D:\archive-scan-service\.venv\python.exe -m pytest tests/integration/test_api.py -k "health or info or device or capability or task or scan" -v`

Expected: FAIL，因为路由尚不存在。

- [ ] **Step 3: 实现全部 JSON 路由**

实现 camelCase 请求解析并兼容 `settings` 包裹对象和直接配置对象；创建任务时保留设备/Capability/扫描参数快照；开始扫描时在 `tasks/{taskId}/originals` 创建目录、生成 `pageId`、覆盖安全的 `outputDir`，只把 Worker 接受的 JSON 命令发送到子进程。停止、完成和失败均严格调用 TaskService 状态机，所有异常转稳定错误信封。

- [ ] **Step 4: 运行接口专项测试**

Run: `D:\archive-scan-service\.venv\python.exe -m pytest tests/integration/test_api.py -k "health or info or device or capability or task or scan" -v`

Expected: PASS。

- [ ] **Step 5: 提交服务与任务接口**

```powershell
git add app/api/health.py app/api/devices.py app/api/tasks.py app/api/responses.py tests/integration/test_api.py
git commit -m "feat: 提供设备Capability和任务接口"
```

### Task 4：页面查询、文件响应和显式删除

**Files:**
- Create: `app/api/pages.py`
- Modify: `app/services/page_service.py`
- Modify: `app/api/tasks.py`
- Test: `tests/integration/test_api.py`

- [ ] **Step 1: 写失败测试**

```python
def test_page_api_returns_metadata_and_jpeg_files(client, seeded_page):
    detail = client.get(
        f"/api/v1/tasks/{seeded_page.task_id}/pages/{seeded_page.page_id}"
    )
    assert detail.json()["data"]["originalUrl"].endswith("/original")

    thumbnail = client.get(detail.json()["data"]["thumbnailUrl"])
    original = client.get(detail.json()["data"]["originalUrl"])
    assert thumbnail.headers["content-type"] == "image/jpeg"
    assert original.headers["content-type"] == "image/jpeg"


def test_page_and_task_delete_remove_files_and_records(client, seeded_page):
    page_response = client.delete(
        f"/api/v1/tasks/{seeded_page.task_id}/pages/{seeded_page.page_id}"
    )
    assert page_response.status_code == 200
    assert not seeded_page.original.exists()
    assert not seeded_page.thumbnail.exists()
```

- [ ] **Step 2: 运行失败测试**

Run: `D:\archive-scan-service\.venv\python.exe -m pytest tests/integration/test_api.py -k "page or thumbnail or original or delete" -v`

Expected: FAIL，因为页面路由和文件删除编排尚不存在。

- [ ] **Step 3: 实现安全路径和页面路由**

在 `PageService` 中增加任务内相对路径解析、页面/任务删除方法；先检查活动状态和全部文件，再删除文件与 SQLite 记录，任何异常返回 `FILE_NOT_FOUND`/`FILE_DELETE_FAILED`。页面接口用 `FileResponse` 固定 `image/jpeg`，页面 JSON 只包含相对路径和本机 API URL；`afterSequence` 只接受非负整数。

- [ ] **Step 4: 运行文件和删除专项测试**

Run: `D:\archive-scan-service\.venv\python.exe -m pytest tests/integration/test_api.py -k "page or thumbnail or original or delete" -v`

Expected: PASS。

- [ ] **Step 5: 提交页面和文件接口**

```powershell
git add app/api/pages.py app/api/tasks.py app/services/page_service.py tests/integration/test_api.py
git commit -m "feat: 提供页面文件和删除接口"
```

### Task 5：WebSocket、入口、文档和完整验证

**Files:**
- Create: `app/api/events.py`
- Create: `run.py`
- Create: `tests/integration/test_websocket.py`
- Modify: `app/main.py`
- Modify: `docs/superpowers/plans/2026-07-15-python扫描服务实现计划.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: 写失败测试**

```python
def test_websocket_receives_task_event_and_applies_task_filter(client, event_hub):
    with client.websocket_connect("/api/v1/events?taskId=task-1") as websocket:
        event_hub.publish({"event": "task_started", "taskId": "task-2", "data": {}})
        event_hub.publish({"event": "task_started", "taskId": "task-1", "data": {}})
        assert websocket.receive_json()["taskId"] == "task-1"
```

- [ ] **Step 2: 运行失败测试**

Run: `D:\archive-scan-service\.venv\python.exe -m pytest tests/integration/test_websocket.py -v`

Expected: FAIL，因为 WebSocket 路由尚不存在。

- [ ] **Step 3: 实现 WebSocket 和入口**

实现服务级事件直发、任务事件过滤、订阅释放和断开处理；`run.py` 调用 `uvicorn.run` 时只能使用 `Settings.host`/`Settings.port`，不得接受命令行覆盖为 `0.0.0.0`。在计划和 `AGENTS.md` 中记录 Task 11 的实现与自动化验证边界，明确不等同于真实扫描验收。

- [ ] **Step 4: 运行专项、全量和静态验证**

```powershell
D:\archive-scan-service\.venv\python.exe -m pytest tests/integration/test_api.py tests/integration/test_websocket.py tests/unit/test_event_hub.py -v
D:\archive-scan-service\.venv\python.exe -m pytest -q
D:\archive-scan-service\.venv\Scripts\ruff.exe check app tests
D:\archive-scan-service\.venv\python.exe -m compileall -q app run.py
git diff --check
```

Expected: 专项和全量测试通过；Ruff、编译检查和 diff 检查退出码均为 0。真实 TWAIN 测试仍按现有 `integration` 标记单独执行，不在 Task 11 自动化结果中冒充通过。

- [ ] **Step 5: 更新 Task 11 状态并提交**

```powershell
git add app tests docs/superpowers/plans/2026-07-15-python扫描服务实现计划.md AGENTS.md pyproject.toml run.py
git commit -m "feat: 提供本机扫描HTTP与事件接口"
```
