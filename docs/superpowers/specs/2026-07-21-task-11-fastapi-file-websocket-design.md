# Task 11 FastAPI、文件接口和 WebSocket 设计

## 目标

在主进程中提供完整的 `/api/v1` 本机 HTTP 接口和 `/api/v1/events` WebSocket，覆盖服务状态、TWAIN 设备、Capability、扫描任务、页面、缩略图、原图和删除操作。接口只绑定 `127.0.0.1:17653`，主进程继续作为 SQLite 唯一写入者，TWAIN DSM、Data Source 和消息循环仍只存在于 Worker 子进程。

## API 契约

除文件响应外，所有接口都返回：

```json
{
  "code": 200,
  "message": "操作成功",
  "data": {}
}
```

失败响应使用稳定的 HTTP 状态、数字 `code` 和业务 `data.errorCode`，不返回 Python 堆栈、请求对象或本机绝对路径：

```json
{
  "code": 4091,
  "message": "扫描仪正在被其他任务占用",
  "data": {
    "errorCode": "SCANNER_BUSY"
  }
}
```

接口矩阵如下：

| 方法 | 路径 | 请求 | 成功数据 |
| --- | --- | --- | --- |
| GET | `/api/v1/health` | 无 | `status`、`workerReady`、`workerPid` |
| GET | `/api/v1/info` | 无 | `serviceName`、`version`、`apiVersion`、`host`、`port`、`architecture` |
| GET | `/api/v1/devices` | 无 | `devices`、`total` |
| GET | `/api/v1/devices/{deviceId}/capabilities` | 无 | `deviceId`、完整 `capabilities`、`count` |
| POST | `/api/v1/devices/{deviceId}/capabilities/resolve` | `settings` 对象，或直接传固定配置对象 | 重新查询后的完整 Capability 快照 |
| POST | `/api/v1/tasks` | `deviceId`，可选 `taskId`、快照对象 | 新建任务记录 |
| GET | `/api/v1/tasks` | 无 | `items`、`tasks`、`total` |
| GET | `/api/v1/tasks/{taskId}` | 无 | 任务记录和 `pageCount` |
| POST | `/api/v1/tasks/{taskId}/scan/start` | `settings` 对象，或直接传固定配置对象 | `SCANNING` 任务记录 |
| POST | `/api/v1/tasks/{taskId}/scan/stop` | 无 | `STOPPING` 任务记录 |
| POST | `/api/v1/tasks/{taskId}/scan/complete` | 无 | `COMPLETED` 任务记录 |
| DELETE | `/api/v1/tasks/{taskId}` | 无 | 已删除任务标识 |
| GET | `/api/v1/tasks/{taskId}/pages` | 可选 `afterSequence` | `items`、`pages`、`total` |
| GET | `/api/v1/tasks/{taskId}/pages/{pageId}` | 无 | 页面元数据和文件 URL |
| GET | `/api/v1/tasks/{taskId}/pages/{pageId}/thumbnail` | 无 | `image/jpeg` 文件 |
| GET | `/api/v1/tasks/{taskId}/pages/{pageId}/original` | 无 | `image/jpeg` 文件 |
| DELETE | `/api/v1/tasks/{taskId}/pages/{pageId}` | 无 | 已删除页面标识 |
| WS | `/api/v1/events` | 可选 `taskId` 查询参数 | 事件信封 |

任务请求使用 camelCase。`taskId` 缺省时由服务生成稳定格式的 UUID 标识；`deviceId` 必填但允许先保存历史任务，再在设备枚举时确认在线状态。扫描开始时服务忽略调用方提供的 `outputDir` 和 `pageId`，由主进程在任务目录内生成，防止请求借接口访问任意文件。

任务、页面、Capability 的 JSON 字段保持现有领域模型的 camelCase 映射：`createdAt`、`updatedAt`、`lastPageSequence`、`errorCode`、`errorMessage`、`originalPath`、`thumbnailPath`、`sha256`、`fileSize`、`currentValue`、`defaultValue`、`getCurrent` 等。对外页面数据只返回任务内相对路径和接口 URL，不返回文件系统绝对路径。

## 主进程装配

`ApplicationContext` 负责装配 `Settings`、`Database`、两个仓储、`TaskService`、`PageService`、`RecoveryService`、`WorkerSupervisor`、Worker 网关和 `EventHub`。FastAPI 的 lifespan 执行以下顺序：

1. 创建目录和数据库，执行恢复；
2. 启动 Worker 并启动一个唯一的事件泵；
3. Worker ready 后发布 `service_started` 和 `worker_started`；
4. 应用关闭时停止接收新请求、停止事件泵、关闭 Worker、最后关闭 SQLite。

Worker 网关在主进程中维护 commandId 等待器、当前设备快照、已打开 Data Source 和 Capability 快照。设备列表、Capability 查询和 resolve 请求由事件泵聚合 `device_listed`/`capabilities_queried` 与 `command_succeeded`，命令失败转换为稳定错误。扫描请求只发送结构化命令和任务目录字符串；`page_file_ready` 由 `PageService` 校验、生成缩略图、登记 SQLite 后才发布 `page_completed`。

Worker 生命周期事件映射如下：

| Worker 事件 | 主进程动作 | 对外事件 |
| --- | --- | --- |
| `scan_started` | 保持 `SCANNING` | `task_started`、`page_started`（如有页面数据） |
| `page_file_ready` | 页面校验、摘要、缩略图和登记 | `page_completed` |
| `scan_stopped` | `STOPPING` → `STOPPED` | `task_stopped` |
| `scan_completed` | `SCANNING` → `COMPLETED` | `task_completed` |
| `scan_failed` | 记录错误并置为 `FAILED` | `task_failed` |
| `worker_unavailable` | 活动任务置为 `FAILED`，保留已完成页面 | `task_failed`、`worker_restarted` |

## 删除和文件安全

页面和任务删除前检查任务不处于 `SCANNING` 或 `STOPPING`。删除页面先读取数据库记录，再把 `original_path` 和 `thumbnail_path` 解析到 `tasks_root/{taskId}` 下并确认文件存在、为普通文件且不能越出任务目录；两个文件都可访问后才删除文件和数据库记录。任务删除先检查全部页面文件和任务目录边界，再删除原图、缩略图、任务目录和 SQLite 记录。任何文件系统异常都返回 `FILE_NOT_FOUND` 或 `FILE_DELETE_FAILED`，不返回虚假成功。

文件接口只接受数据库中的页面记录，固定返回 `FileResponse(..., media_type="image/jpeg")`。`afterSequence` 必须是非负整数；任务、页面和设备标识都拒绝空字符串、绝对路径、`.`、`..` 和包含路径分隔符的值。

## WebSocket

`EventHub` 为每个 WebSocket 连接创建有界队列，支持 Worker 线程同步发布和 asyncio WebSocket 异步消费；队列满时丢弃最旧事件，不阻塞扫描。连接可以用 `taskId` 查询参数过滤任务事件，但服务级事件始终发送。事件信封固定为：

```json
{
  "event": "page_completed",
  "taskId": "task-1",
  "timestamp": "2026-07-21T12:00:00.000+00:00",
  "data": {}
}
```

事件名称覆盖 `service_started`、`worker_started`、`worker_restarted`、`device_list_changed`、`task_created`、`task_started`、`page_started`、`page_completed`、`task_stopping`、`task_stopped`、`task_completed`、`task_failed`、`page_deleted` 和 `task_deleted`。关闭连接、取消订阅和服务关闭都必须释放队列。

## CORS 和错误边界

CORS 来源从本机配置读取；未配置时不扩大为任意来源，服务仍只绑定回环地址。FastAPI 参数校验、未知路由、领域异常、Worker 异常和文件异常统一转换为上述错误信封；未知异常只返回 `INTERNAL_ERROR` 和固定中文消息，并通过现有日志记录诊断信息。

## 验证范围

接口测试使用 Fake Worker 和临时 SQLite/任务目录，逐项覆盖全部 HTTP 路由、统一响应、任务状态错误、设备和 Capability 聚合、增量页面、JPEG 文件响应、路径越界、页面/任务删除、CORS 和未知错误脱敏。WebSocket 测试覆盖服务事件、任务过滤、页面完成广播、队列订阅释放和连接关闭。专项测试通过后再运行全量 pytest、Ruff、Python 编译检查和 `git diff --check`；不把接口自动化测试当作真实 TWAIN 或 Task 14 批量验收。
