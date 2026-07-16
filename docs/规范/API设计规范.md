# API设计规范

## 1. 基础约定

```text
地址：http://127.0.0.1:17653
前缀：/api/v1
格式：application/json
```

文件读取接口直接返回文件响应，其他接口使用统一JSON结构。

## 2. 统一响应

成功：

```json
{
  "code": 200,
  "message": "操作成功",
  "data": {}
}
```

失败：

```json
{
  "code": 5001,
  "message": "扫描仪正在被其他任务占用",
  "data": {
    "errorCode": "SCANNER_BUSY"
  }
}
```

禁止向调用方返回Python堆栈和本机任意绝对路径。

## 3. 服务接口

```text
GET /api/v1/health
GET /api/v1/info
```

## 4. 设备接口

```text
GET  /api/v1/devices
GET  /api/v1/devices/{deviceId}/capabilities
POST /api/v1/devices/{deviceId}/capabilities/resolve
```

`resolve`只用于按上游选择重新计算依赖 Capability，不开始扫描。

## 5. 任务接口

```text
POST   /api/v1/tasks
GET    /api/v1/tasks
GET    /api/v1/tasks/{taskId}
POST   /api/v1/tasks/{taskId}/scan/start
POST   /api/v1/tasks/{taskId}/scan/stop
POST   /api/v1/tasks/{taskId}/scan/complete
DELETE /api/v1/tasks/{taskId}
```

## 6. 页面接口

```text
GET    /api/v1/tasks/{taskId}/pages
GET    /api/v1/tasks/{taskId}/pages/{pageId}
GET    /api/v1/tasks/{taskId}/pages/{pageId}/thumbnail
GET    /api/v1/tasks/{taskId}/pages/{pageId}/original
DELETE /api/v1/tasks/{taskId}/pages/{pageId}
```

页面增量查询：

```text
GET /api/v1/tasks/{taskId}/pages?afterSequence=126
```

## 7. WebSocket

```text
WS /api/v1/events
```

事件统一包含：

```json
{
  "event": "page_completed",
  "taskId": "任务标识",
  "timestamp": "ISO 8601时间",
  "data": {}
}
```

## 8. 状态校验

- 扫描中只允许停止扫描和读取状态。
- 当前扫描页不能删除。
- 同一时间创建第二个扫描动作返回 `SCANNER_BUSY`。
- 删除任务前必须确认没有活动扫描和文件读取。

## 9. 错误码

```text
TWAIN_DSM_NOT_FOUND
TWAIN_SOURCE_NOT_FOUND
TWAIN_SOURCE_ENUMERATION_FAILED
TWAIN_SOURCE_OPEN_FAILED
TWAIN_CAPABILITY_QUERY_FAILED
TWAIN_CAPABILITY_SET_FAILED
TWAIN_FILE_TRANSFER_UNSUPPORTED
TWAIN_JPEG_UNSUPPORTED
SCANNER_BUSY
SCANNER_OFFLINE
PAPER_JAM
DISK_SPACE_LOW
TASK_NOT_FOUND
TASK_STATE_INVALID
PAGE_NOT_FOUND
FILE_NOT_FOUND
WORKER_UNAVAILABLE
SCAN_FAILED
```

ADF无纸是正常完成，不使用错误码。
