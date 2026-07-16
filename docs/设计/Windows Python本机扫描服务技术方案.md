# Windows Python 本机扫描服务技术方案

## 1. 文档说明

### 1.1 编写目的

本文档用于说明文书管理系统在 Windows 客户端上使用 Python 开发本机扫描服务的技术方案，明确服务范围、进程架构、TWAIN设备接入、Capability管理、扫描任务、本地文件、接口、异常恢复、部署和验收要求。

本文档只设计本机扫描服务，不设计 Vue 页面、档案条目选择、档案分组、业务批次上传页面和业务后端实现。

### 1.2 文档信息

```text
文档版本：1.0
编写日期：2026-07-14
目标设备：KODAK i2600 Scanner
目标系统：Windows 64位
```

## 2. 已确认范围

### 2.1 第一版实现范围

```text
Windows 64位
Python 64位
64位TWAINDSM.DLL
64位TWAIN Data Source
pytwain 2.3.0
无厂商界面扫描
TWSX_FILE文件传输
每页独立JPEG原图
Python仅额外生成预览缩略图
SQLite任务持久化
登录用户级托盘程序
FastAPI本机HTTP接口
主进程 + TWAIN工作子进程
```

### 2.2 第一版边界

第一版只实现 Windows 64位 TWAIN扫描链路，不保留其他操作系统、扫描协议或驱动适配器的空模块。

暂不实现：

```text
TWAIN 32位
TWSX_MEMORY
TWSX_NATIVE
Windows Service模式
Python原图加工
OCR
PDF处理
原图旋转、裁剪、纠偏和重新压缩
业务后端通信
Vue业务页面设计
自动在线升级
```

## 3. 总体架构

```text
Windows登录用户会话
│
├─ Python主进程
│  ├─ 托盘程序
│  ├─ FastAPI
│  ├─ WebSocket事件
│  ├─ SQLite任务数据库
│  ├─ 原图和缩略图文件管理
│  ├─ 缩略图生成
│  └─ TWAIN工作进程管理
│
└─ TWAIN工作子进程
   ├─ pytwain 2.3.0
   ├─ 64位TWAINDSM.DLL
   ├─ 64位TWAIN Data Source
   ├─ Capability查询与设置
   ├─ TWAIN消息循环
   └─ TWSX_FILE文件扫描
```

本机服务固定监听：

```text
http://127.0.0.1:17653
```

运行方式：

```python
uvicorn.run(
    app,
    host="127.0.0.1",
    port=17653
)
```

不监听 `0.0.0.0`，不允许通过客户机局域网IP或公网IP访问。

## 4. 进程职责

### 4.1 主进程

主进程负责：

- 启动和维护托盘程序。
- 提供 FastAPI HTTP接口。
- 提供 WebSocket事件接口。
- 读写 SQLite任务数据库。
- 管理任务目录、原图、缩略图和日志。
- 使用 Pillow生成缩略图，但不修改原始 JPEG。
- 启动、监控、停止和重建 TWAIN工作子进程。
- 校验任务状态和接口请求。
- 对外返回统一响应和错误码。

只有主进程可以写 SQLite。

### 4.2 TWAIN工作子进程

TWAIN工作子进程负责：

- 独占加载和关闭 `TWAINDSM.DLL`。
- 枚举全部64位 TWAIN Data Source。
- 打开用户选择的设备。
- 查询全部标准和厂商私有 Capability。
- 接收主进程命令并设置 Capability。
- 使用 `show_ui=False` 启用 Data Source。
- 运行 TWAIN消息循环。
- 通过 `TWSX_FILE`让驱动直接写入单页 JPEG。
- 向主进程发送设备、Capability、页面和任务事件。

工作子进程禁止：

- 写入 SQLite。
- 生成缩略图。
- 修改历史任务。
- 访问业务后端。
- 保存业务凭证。

## 5. TWAIN基础方案

### 5.1 基础组件

```text
Python：64位
TWAIN DSM：64位TWAINDSM.DLL
TWAIN Data Source：64位
Python库：pytwain 2.3.0
许可证：GPLv2
```

### 5.2 pytwain使用边界

不使用 `pytwain.acquire_file()` 的 BMP转 JPEG高级封装。

使用低层接口：

```text
file_xfer_params
xfer_image_by_file
```

固定设置：

```text
ICAP_XFERMECH = TWSX_FILE
ICAP_IMAGEFILEFORMAT = TWFF_JFIF
show_ui = False
modal_ui = False
```

如果设备不支持 `TWSX_FILE` 或 JPEG/JFIF文件传输，第一版直接判定设备不兼容，不降级到内存传输或原生传输。

### 5.3 项目内扩展

需要在项目内补充：

```text
MSG_QUERYSUPPORT封装
TWQC_*操作位解析
CAP_SUPPORTEDCAPS完整遍历
全部Capability容器解析
私有Capability原始信息返回
通用Capability设置
设置后MSG_GETCURRENT确认
TWRC_CHECKSTATUS处理
标准Capability中文映射
```

建议模块：

```text
app/scanner/
├─ pytwain_adapter.py
├─ capability_reader.py
├─ capability_writer.py
├─ capability_mapper.py
├─ message_loop.py
└─ twain_constants.py
```

## 6. 设备管理

### 6.1 设备枚举

```text
调用方请求设备列表
→ 主进程发送LIST_DEVICES
→ 工作子进程打开DSM
→ 枚举全部64位TWAIN Data Source
→ 返回设备列表
```

设备信息：

```json
{
  "deviceId": "稳定设备标识",
  "manufacturer": "厂商",
  "productFamily": "产品系列",
  "productName": "设备名称",
  "protocolMajor": 2,
  "protocolMinor": 5,
  "online": true
}
```

第一版目标设备：

```text
KODAK i2600 Scanner
```

本机可以枚举多台64位 TWAIN设备，由调用方选择；同一时间只允许一台设备执行扫描。

### 6.2 设备选择规则

- 保存上次成功使用的设备作为默认值。
- 默认设备不存在时不自动切换到其他设备。
- 创建任务后固定 `deviceId`。
- 恢复任务时仍显示原设备信息。
- 多设备可以查询，但不支持并发扫描。

## 7. Capability管理

### 7.1 查询流程

```text
打开Data Source
→ 查询CAP_SUPPORTEDCAPS
→ 遍历全部Capability
→ 查询MSG_QUERYSUPPORT
→ 查询MSG_GET
→ 查询MSG_GETCURRENT
→ 查询MSG_GETDEFAULT
→ 解析容器和Item类型
→ 返回标准及私有Capability
```

### 7.2 容器类型

```text
TW_ONEVALUE
TW_ENUMERATION
TW_RANGE
TW_ARRAY
```

### 7.3 Item类型

```text
TWTY_INT8
TWTY_INT16
TWTY_INT32
TWTY_UINT8
TWTY_UINT16
TWTY_UINT32
TWTY_BOOL
TWTY_FIX32
TWTY_FRAME
TWTY_STR32
TWTY_STR64
TWTY_STR128
TWTY_STR255
```

### 7.4 返回结构

标准 Capability返回中文名称和业务含义。

私有 Capability返回：

```json
{
  "capabilityId": 32769,
  "capabilityHex": "0x8001",
  "capabilityName": null,
  "custom": true,
  "containerType": "TW_ENUMERATION",
  "itemType": "TWTY_UINT16",
  "operations": {
    "get": true,
    "set": true,
    "getCurrent": true,
    "getDefault": true,
    "reset": false
  },
  "currentValue": 1,
  "defaultValue": 0,
  "values": [0, 1, 2],
  "source": {
    "manufacturer": "设备厂商",
    "productName": "设备名称"
  }
}
```

私有参数没有厂商映射时保留原始编号和值；后续可以通过配置文件补充中文名称和枚举说明。

### 7.5 设置规则

1. 只能设置当前设备查询结果中存在的 Capability。
2. 必须声明支持 `TWQC_SET`。
3. 枚举值必须来自驱动返回列表。
4. 范围值必须符合最小值、最大值和步长。
5. 保留原始 TWAIN Item类型，不能全部转成字符串。
6. 设置后调用 `MSG_GETCURRENT`确认最终值。
7. 驱动调整参数时返回请求值和实际值。
8. 单个参数设置失败时返回明确错误。
9. `DAT_CUSTOMDSDATA`只支持整块保存和恢复，不解析内部字段。

### 7.6 参数依赖顺序

```text
扫描来源
→ 单双面
→ 颜色和位深
→ 纸张尺寸和方向
→ 分辨率
→ JPEG质量和压缩
→ 标准图像增强参数
→ 厂商私有参数
```

设置关键上游参数后，重新查询受影响的下游 Capability。

## 8. 扫描模式和参数

### 8.1 进纸模式

根据设备实际 Capability动态支持：

```text
平板
ADF单面
ADF双面
```

参数映射：

```text
平板：
CAP_FEEDERENABLED = FALSE

ADF单面：
CAP_FEEDERENABLED = TRUE
CAP_DUPLEXENABLED = FALSE
CAP_AUTOFEED = TRUE

ADF双面：
CAP_FEEDERENABLED = TRUE
CAP_DUPLEXENABLED = TRUE
CAP_AUTOFEED = TRUE
```

未连接可选平板附件时，不把平板模式标记为可用。

### 8.2 JPEG质量

JPEG质量通过 `ICAP_JPEGQUALITY` 动态查询和设置：

- 驱动返回枚举时返回枚举值。
- 驱动返回范围时返回最小值、最大值和步长。
- 驱动不支持时不提供可设置状态，使用驱动默认值。
- 设置后读取最终生效值。

### 8.3 全部参数

本机服务查询并返回驱动声明的全部标准和私有 Capability。自动裁边、自动纠偏、自动旋转、空白页处理、背景去除等功能如果由 TWAIN驱动暴露，则由驱动执行；Python不对原图进行二次处理。

## 9. 页面文件模型

### 9.1 文件粒度

每一面生成一个独立页面：

```text
一张纸正面 = 一个pageId和JPEG文件
一张纸背面 = 一个pageId和JPEG文件
```

双面顺序按 Data Source返回顺序保存，例如：

```text
page-000001：第1张正面
page-000002：第1张背面
page-000003：第2张正面
page-000004：第2张背面
```

本机服务不合并正反面，也不自行判断正反面关系。

### 9.2 原图规则

- 原图固定为单页 JPEG。
- 原图由 TWAIN Data Source直接写入。
- Python不重新编码原图。
- Python不旋转、裁剪、纠偏、调整亮度或压缩原图。
- 每页使用稳定 `pageId`。
- 原始 `sequence`在页面删除后不重新编号。

### 9.3 页面操作

本机服务只提供：

```text
查询页面
读取缩略图
读取原始JPEG
删除页面
```

不提供：

```text
页面排序
档案分组
业务页码
旋转参数
上传状态
```

## 10. 本地存储

### 10.1 根目录

```text
%LOCALAPPDATA%\ArchiveScanService\
```

目录结构：

```text
%LOCALAPPDATA%\ArchiveScanService\
├─ config\
│  └─ service.json
├─ data\
│  ├─ scan_service.db
│  └─ tasks\
│     └─ {taskId}\
│        ├─ originals\
│        │  └─ {pageId}.jpg
│        └─ thumbnails\
│           └─ {pageId}.jpg
└─ logs\
   ├─ service.log
   ├─ twain-worker.log
   └─ error.log
```

### 10.2 SQLite

SQLite保存：

- 任务记录。
- 页面记录。
- 设备快照。
- Capability快照。
- 扫描参数快照。
- 状态和错误信息。
- 文件路径、大小、尺寸和 SHA-256。
- 创建时间和更新时间。

原图和缩略图不保存到 SQLite二进制字段。

### 10.3 页面写入顺序

```text
生成pageId
→ 设置TWAIN临时文件路径
→ Data Source写入临时JPEG
→ 文件传输成功
→ 原子重命名为{pageId}.jpg
→ 读取文件大小和尺寸
→ 计算SHA-256
→ 生成缩略图
→ SQLite事务写入页面记录
→ 推送page_completed
```

未完整写入的临时文件不能作为有效页面。

## 11. 缩略图

Python允许使用 Pillow生成预览缩略图，但不得修改原始 JPEG。

默认参数：

```text
最长边：320像素
格式：JPEG
质量：75
```

规则：

- 缩略图只用于预览。
- 缩略图生成失败时保留原图。
- 缩略图可以重新生成。
- 重新生成前后原图 SHA-256必须保持不变。
- 缩略图损坏不影响原图有效性。

## 12. 扫描任务

### 12.1 任务状态

```text
CREATED      已创建
SCANNING     扫描中
STOPPING     正在停止
STOPPED      已停止
COMPLETED    扫描完成
FAILED       扫描失败
CANCELLED    已取消
```

### 12.2 多任务规则

- 本机可以保存多个历史任务。
- 同一时间只允许一个任务处于 `SCANNING`。
- 创建新任务不要求删除旧任务。
- 已完成、停止、失败和取消任务都可以恢复查看。
- `STOPPED`、`FAILED`和`COMPLETED`任务允许继续补扫。
- 补扫页面追加到任务末尾。
- 每次补扫前重新检测设备和查询 Capability。
- 任务不保存档案编号、档案分组、业务批次和上传状态。

### 12.3 ADF规则

```text
开始扫描
→ 自动连续进纸
→ 每完成一面保存一页
→ ADF无纸
→ 正常完成本轮扫描
```

ADF无纸是正常结束，不作为错误。

### 12.4 平板规则

```text
开始扫描
→ 完成一页
→ 本轮扫描暂停
→ 调用方决定继续扫描或完成任务
```

### 12.5 停止和恢复

- 用户可以请求停止扫描。
- 已完成页面保留。
- 当前未完成页面不创建页面记录。
- 卡纸、设备断开和驱动异常标记为失败。
- 服务重启后不自动继续驱动扫描仪。
- 中断任务恢复为 `STOPPED`或`FAILED`。
- 可以在原任务上继续补扫。

## 13. 本机HTTP接口

统一前缀：

```text
/api/v1
```

### 13.1 服务

```text
GET /api/v1/health
GET /api/v1/info
```

### 13.2 设备

```text
GET  /api/v1/devices
GET  /api/v1/devices/{deviceId}/capabilities
POST /api/v1/devices/{deviceId}/capabilities/resolve
```

`resolve` 用于根据已经选择的上游参数重新查询依赖参数。

### 13.3 任务

```text
POST   /api/v1/tasks
GET    /api/v1/tasks
GET    /api/v1/tasks/{taskId}
POST   /api/v1/tasks/{taskId}/scan/start
POST   /api/v1/tasks/{taskId}/scan/stop
POST   /api/v1/tasks/{taskId}/scan/complete
DELETE /api/v1/tasks/{taskId}
```

### 13.4 页面

```text
GET    /api/v1/tasks/{taskId}/pages
GET    /api/v1/tasks/{taskId}/pages/{pageId}
GET    /api/v1/tasks/{taskId}/pages/{pageId}/thumbnail
GET    /api/v1/tasks/{taskId}/pages/{pageId}/original
DELETE /api/v1/tasks/{taskId}/pages/{pageId}
```

支持增量查询：

```text
GET /api/v1/tasks/{taskId}/pages?afterSequence=126
```

### 13.5 WebSocket

```text
WS /api/v1/events
```

事件类型：

```text
service_started
worker_started
worker_restarted
device_list_changed
task_created
task_started
page_started
page_completed
task_stopping
task_stopped
task_completed
task_failed
page_deleted
task_deleted
```

不承诺单页扫描百分比。

### 13.6 统一响应

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

## 14. 进程间通信

### 14.1 命令

命令的线上 `type` 使用小写下划线命名：

```text
worker_init
enumerate_devices
open_source
query_capabilities
resolve_capabilities
start_scan
stop_scan
close_source
shutdown
```

每条命令包含唯一 `commandId`。

### 14.2 事件

事件的线上 `type` 使用小写下划线命名：

```text
command_succeeded
command_failed
worker_ready
device_listed
capabilities_queried
scan_started
page_started
page_file_ready
scan_stopped
scan_completed
scan_failed
worker_heartbeat
```

每条消息都包含协议 `version`、消息 `kind`、可判别的 `type` 和 JSON `payload`；命令必须包含唯一 `commandId`，关联任务时包含 `taskId`。示例：

```json
{
  "version": 1,
  "kind": "command",
  "type": "start_scan",
  "commandId": "cmd-1",
  "taskId": "task-1",
  "payload": {
    "deviceId": "device-1",
    "settings": {
      "ICAP_XRESOLUTION": 300
    }
  }
}
```

主进程和工作子进程只传输命令、状态和文件路径字符串，不传输原始图片内容、SQLite 连接或 TWAIN 对象。未知协议版本、未知消息类型、命令/事件方向不匹配和非 JSON 值必须拒绝。`page_file_ready` 只表示原图文件已完成传输并提供路径，主进程完成校验、摘要、缩略图和数据库写入后再发布业务页面完成事件。

事件关联约束如下：命令结果、设备列表、Capability查询和扫描生命周期事件必须带 `commandId`；扫描开始、页面、停止、完成和失败事件还必须带 `taskId`；`worker_ready` 和 `worker_heartbeat` 是工作进程级事件，可以不带任务或命令标识。`page_file_ready.payload.path` 必须是非空字符串，任务目录内的规范化路径校验由主进程页面接收层执行。

### 14.3 串行化

TWAIN工作子进程的 DSM、Data Source和 Capability操作严格串行。

扫描期间只接受停止扫描命令，其他设备和 Capability请求由主进程拒绝或排队。

## 15. 异常恢复

### 15.1 工作进程异常

工作子进程异常退出后：

1. 主进程检测进程退出。
2. 当前任务标记为 `FAILED`。
3. 保存退出码和最后错误信息。
4. 已完成页面继续保留。
5. 清理未完成临时文件。
6. 自动启动新的 TWAIN工作子进程。
7. 新进程重新加载 DSM。
8. 不自动继续原扫描任务。

### 15.2 无响应

工作子进程发送心跳。心跳超时只标记无响应，不立即强制杀死进程。

用户可以通过托盘执行“重启扫描组件”。强制重启只影响工作子进程，不影响主进程、HTTP接口和历史任务。

### 15.3 启动检查

主进程启动后：

- 从 SQLite恢复任务和页面。
- 扫描中任务改为 `STOPPED`或`FAILED`。
- 数据库存在但原图缺失的页面标记为文件缺失。
- 原图存在但数据库无记录的文件移入隔离目录或记录诊断日志。
- 不自动把孤立文件认定为有效页面。

## 16. 页面和任务删除

### 16.1 页面删除

- 扫描中的当前页面不能删除。
- 已完成页面可以删除。
- 同时删除原图、缩略图和 SQLite记录。
- 删除失败不返回虚假成功。
- 其他页面 `pageId`和原始 `sequence`保持不变。

### 16.2 任务删除

删除任务时处理：

```text
任务记录
页面记录
原图
缩略图
任务目录
```

失败、取消、中断和未处理完成的任务不自动删除，一直保留到用户明确删除。

业务流程完成后，调用方可以明确请求删除本机任务。本机服务不根据时间自动清理。

## 17. 托盘程序

托盘状态：

```text
绿色：服务正常
蓝色：正在扫描
黄色：工作进程无响应或正在重启
红色：服务或DSM异常
灰色：扫描组件未启动
```

托盘菜单：

```text
服务状态
当前扫描任务
打开数据目录
打开日志目录
重启扫描组件
重启本机服务
设置开机启动
退出程序
```

第一版不提供完整本机任务管理窗口。

正常退出流程：

```text
停止接收新请求
→ 请求停止当前扫描
→ 关闭Data Source
→ 关闭DSM
→ 关闭工作子进程
→ 关闭SQLite
→ 退出托盘程序
```

存在扫描中任务时，需要用户确认后退出。

## 18. 配置和日志

### 18.1 配置

```json
{
  "host": "127.0.0.1",
  "port": 17653,
  "thumbnail": {
    "maxSize": 320,
    "jpegQuality": 75
  },
  "storage": {
    "minimumFreeSpaceBytes": 10737418240
  },
  "logging": {
    "level": "INFO",
    "maxFileSizeBytes": 10485760,
    "backupCount": 10
  }
}
```

`host`固定为 `127.0.0.1`，不允许配置为 `0.0.0.0`。

### 18.2 日志

```text
service.log
twain-worker.log
error.log
```

记录：

- 程序启动和退出。
- Python、Windows、pytwain和 DSM版本。
- 设备枚举。
- Capability查询和设置。
- TWAIN状态迁移。
- 任务和页面事件。
- 工作进程退出和重建。
- SQLite、磁盘和文件异常。

不得记录业务凭证、用户密码、原始图片内容和无关本机文件内容。

## 19. Python工程结构

```text
archive-scan-service/
├─ app/
│  ├─ main.py
│  ├─ tray.py
│  ├─ api/
│  │  ├─ health.py
│  │  ├─ devices.py
│  │  ├─ tasks.py
│  │  ├─ pages.py
│  │  └─ events.py
│  ├─ scanner/
│  │  ├─ pytwain_adapter.py
│  │  ├─ capability_reader.py
│  │  ├─ capability_writer.py
│  │  ├─ capability_mapper.py
│  │  ├─ message_loop.py
│  │  └─ twain_constants.py
│  ├─ worker/
│  │  ├─ process.py
│  │  ├─ commands.py
│  │  └─ events.py
│  ├─ services/
│  │  ├─ task_service.py
│  │  ├─ image_service.py
│  │  ├─ storage_service.py
│  │  └─ worker_service.py
│  ├─ models/
│  │  ├─ task.py
│  │  ├─ page.py
│  │  ├─ device.py
│  │  └─ capability.py
│  └─ repositories/
│     ├─ task_repository.py
│     └─ page_repository.py
├─ tests/
├─ packaging/
│  └─ windows/
├─ pyproject.toml
└─ README.md
```

依赖：

```text
FastAPI
Uvicorn
Pydantic
pytwain==2.3.0
Pillow
pystray
```

优先使用标准库：

```text
sqlite3
multiprocessing
queue
threading
pathlib
hashlib
logging
json
uuid
```

## 20. Windows部署

### 20.1 打包

使用64位 PyInstaller打包，客户机不需要预装 Python。

入口必须包含：

```python
if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
```

### 20.2 安装包

使用 Inno Setup生成 Windows安装包。

安装程序负责：

- 检测64位 Windows。
- 安装程序文件。
- 检查 `TWAINDSM.DLL`。
- 创建当前用户数据目录。
- 设置当前用户登录后自动启动。
- 创建开始菜单快捷方式。
- 安装许可证和第三方组件说明。
- 提供卸载程序。

程序运行在当前登录用户会话，不注册 Windows Service。

### 20.3 卸载

默认卸载：

- 删除程序文件。
- 删除自动启动项。
- 停止托盘和工作子进程。
- 保留扫描任务和日志。

只有用户明确选择时才删除本机扫描任务。

## 21. GPLv2交付要求

客户试用也按对外分发处理。

试用和正式安装包必须包含：

```text
LICENSES/
├─ GPL-2.0.txt
├─ pytwain-LICENSE.txt
└─ THIRD-PARTY-NOTICES.md
```

同时准备：

```text
SOURCE/
├─ pytwain-2.3.0/
├─ pytwain项目内修改/
└─ 构建及依赖说明/
```

要求：

- 标明 `pytwain 2.3.0`、许可证和来源。
- 保留原版权声明。
- 记录项目内修改。
- 明确对应源代码获取方式。
- 正式交付前完成许可证合规确认。
- 未完成合规检查前不得向客户分发试用包。

## 22. 主要错误码

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
ADF_EMPTY
PAPER_JAM
DISK_SPACE_LOW
TASK_NOT_FOUND
TASK_STATE_INVALID
PAGE_NOT_FOUND
FILE_NOT_FOUND
WORKER_UNAVAILABLE
SCAN_FAILED
```

ADF无纸属于正常结束，不作为错误返回。

## 23. KODAK i2600验收标准

### 23.1 设备和Capability

- 能够发现 KODAK i2600。
- 能够无界面打开 Data Source。
- 不弹出厂商设置窗口。
- 能够查询全部标准和私有 Capability。
- 能够识别 ADF单面和 ADF双面。
- 未连接平板附件时不返回可用平板模式。
- 支持 `TWSX_FILE + JPEG/JFIF`。
- Capability设置后能够读取实际生效值。

至少验证：

```text
CAP_FEEDERENABLED
CAP_DUPLEX
CAP_DUPLEXENABLED
CAP_AUTOFEED
CAP_FEEDERLOADED
CAP_XFERCOUNT
ICAP_XRESOLUTION
ICAP_YRESOLUTION
ICAP_PIXELTYPE
ICAP_BITDEPTH
ICAP_SUPPORTEDSIZES
ICAP_ORIENTATION
ICAP_XFERMECH
ICAP_IMAGEFILEFORMAT
ICAP_COMPRESSION
ICAP_JPEGQUALITY
```

### 23.2 单面压力测试

```text
300张纸
ADF单面
300 DPI
JPEG
```

要求：

- 生成300个独立 JPEG原图。
- 生成300个缩略图。
- 不丢页、不重复页。
- 页面顺序正确。
- SQLite记录和文件数量一致。
- 扫描期间主进程接口保持可用。

### 23.3 双面压力测试

```text
150张双面纸
ADF双面
300 DPI
JPEG
```

要求：

- 生成300个独立 JPEG原图。
- 正面和背面分别生成独立 `pageId`。
- 不合并正反面。
- 不丢面、不重复面。

### 23.4 连续补纸

```text
一批ADF扫描完成
→ ADF无纸正常结束
→ 在同一个任务中继续扫描
→ 新页面追加到原任务末尾
```

已有 `pageId`保持不变，`sequence`继续递增。

### 23.5 恢复和异常

测试：

- 刷新调用页面。
- 关闭浏览器。
- 正常退出托盘程序。
- 强制终止主进程。
- 强制终止工作子进程。
- Windows注销后重新登录。
- ADF无纸。
- 卡纸。
- 设备断开。
- 磁盘空间不足。

要求：

- 已完成原图和缩略图不丢失。
- 任务和页面可以恢复。
- 中断任务不自动继续扫描。
- 工作子进程异常不导致主进程退出。
- 已完成页面可以继续读取和删除。

### 23.6 稳定性

- 连续完成不少于3轮300页任务。
- 主进程内存不随累计扫描页数持续线性增长。
- 任务结束后 Data Source能够关闭。
- 历史任务查询不预加载原图。
- 扫描期间健康检查接口正常响应。
- 日志按大小轮转。

### 23.7 许可证

客户试用包交付前：

- 包含 GPLv2许可证文本。
- 包含 `pytwain 2.3.0`第三方组件说明。
- 包含修改记录。
- 明确对应源代码获取方式。
- 完成许可证合规确认。

## 24. 最终结论

第一版采用 Windows 64位 Python本机扫描服务，通过 `pytwain 2.3.0` 和64位 `TWAINDSM.DLL` 调用 KODAK i2600扫描仪。

程序采用托盘和 FastAPI主进程加长期 TWAIN工作子进程的双进程架构。TWAIN驱动通过 `TWSX_FILE`直接输出单页 JPEG，Python只保存文件、生成独立缩略图和管理本机扫描任务，不修改原图，不访问业务后端。

本机服务支持全部标准和私有 Capability查询与设置、ADF单面和双面、平板能力动态识别、多个历史任务、单活动扫描任务、完整任务恢复、页面读取和删除，以及数百页连续扫描。
