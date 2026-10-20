# KODAK i2000系列驱动测试与验收

## 1. 环境

```text
Windows 64位
Python 3.12.13 64位运行时（客户测试目录内提供）
64位TWAINDSM.DLL
KODAK i2000系列 64位TWAIN Data Source（kds_i2000.inf）
本机SSD
```

## 2. 设备

- 能发现 `KODAK Scanner: i2000` Data Source。
- 能无界面打开Data Source。
- 能查询全部标准和私有Capability。
- 能识别ADF单面和双面。
- 未连接平板附件时不返回可用平板。
- 能验证 `TWSX_FILE + JPEG/JFIF`。

## 3. Capability

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

验证查询、容器、Item类型、允许值、当前值、默认值、设置结果；设备支持 `MSG_GETCURRENT` 时验证最终生效值，不支持时记录 `readbackUnavailable`，不把缺少回读单独判定为设置失败。

### 3.1 Task 7.5 真实 Capability 冒烟探测

Task 7.5 是 Task 8 文件传输开发前的只读真机前置关卡，不替代 Task 14 正式全量验收。必须在实际连接 KODAK i2400、使用 `kds_i2000.inf` 和 64 位 `KODAK Scanner: i2000` Data Source 的环境中执行。

执行步骤：

1. 在 TWAIN 工作进程内以 `show_ui=False` 打开目标 Data Source，确认 DSM、Data Source 和进程均为 64 位。
2. 读取 `CAP_SUPPORTEDCAPS`，确认返回的 Capability 列表可遍历。
3. 对每个 Capability 调用 `MSG_QUERYSUPPORT`，记录 `TWQC_GET`、`TWQC_GETCURRENT`、`TWQC_GETDEFAULT` 和 `TWQC_SET` 操作位。
4. 按可用消息读取当前值和默认值，保留 Capability 编号、标准/私有标识、容器类型、原始 Item 类型、原始值、操作位及单项 `queryError`；操作位不作为 Task 8 固定配置设置的唯一门槛。
5. 关闭 Data Source，确认查询过程无崩溃、挂起、厂商界面弹出或资源未释放。

本步骤只做 Capability 只读查询，不执行任意 Capability 设置、不启动扫描、不验证 JPEG 文件传输。未设置 `RUN_TWAIN_CAPABILITY_MANUAL=1` 时的自动跳过不能记为通过；真实输出必须保存 DSM 路径、Data Source 身份、驱动入口、Capability 数量和逐项查询结果。探测失败时保留原始结果并暂停 Task 8，不得标记为驱动兼容通过。

### 3.2 Task 7.5 实测结果（2026-07-20）

- Python：`D:\archive-scan-service\.venv\python.exe`，Python 3.12.13，x64。
- DSM：`C:\Windows\System32\TWAINDSM.dll`，x64。
- Data Source：`Eastman Kodak / Document Imaging / KODAK Scanner: i2000`，TWAIN 协议 2.4，驱动信息 `KDS v16.1 2018/2/7`。
- 驱动入口：`kds_i2000.inf`；当前物理设备记录为 KODAK i2400，Windows PnP 名称显示为 `KODAK i2800 Scanner`。
- 工作进程以 `show_ui=False` 打开 Data Source，完成 Capability 查询后正常关闭；未执行 Capability 设置、图像扫描或文件传输。
- `CAP_SUPPORTEDCAPS` 返回并遍历 156 项：`TW_ONEVALUE` 46 项、`TW_ENUMERATION` 79 项、`TW_RANGE` 22 项、`TW_ARRAY` 9 项；记录了每项编号、操作位、容器类型、Item 类型、当前值、默认值和 `queryError`。
- 156 项的 `MSG_QUERYSUPPORT` 记录均为 `set=false`、`getCurrent=false`、`reset=false`，同时保留 `get` 和 `getDefault` 结果；该操作位结果不阻断固定业务配置字段或 Task 8 文件传输。
- 其中 33 项由驱动返回 `BadCapability`，均保留为逐项 `queryError`，没有中断其他 Capability 查询；该结果不等同于设置兼容性或正式扫描验收通过。
- 逐项记录：[task-7.5-2026-07-20.json](实测记录/task-7.5-2026-07-20.json)。

### 3.3 固定业务配置与 Capability 快照

前端使用固定业务配置字段，不根据 156 项 Capability 动态生成配置表。后端通过标准 Capability 名称/编号映射固定字段，并使用 Capability 快照中的候选值、范围、当前值、默认值和查询错误进行校验。设置以 `MSG_SET` 返回状态为准；支持 `MSG_GETCURRENT` 时回读，不支持时记录 `readbackUnavailable`，实际文件传输结果作为设置链路的补充证据。

### 3.4 Task 8 真实设备执行记录

2026-07-21 首次执行 Task 8 单页 JPEG 集成测试时，Windows PnP 显示 `KODAK i2800 Scanner` 不在场，打开 `KODAK Scanner: i2000` Data Source 返回 `TWCC_NODS`；该次没有执行 Capability 设置、`MSG_XFERREADY`、文件传输或 JPEG 输出验证。原始失败记录保留在：[task-8-2026-07-21.json](实测记录/task-8-2026-07-21.json)。

设备重新连接后再次执行同一测试，PnP 状态为 `Status=OK`、`Present=True`。Data Source 成功以 `show_ui=False` 打开并返回 `MSG_XFERREADY`，通过 `TWSX_FILE + TWFF_JFIF` 生成 `scan-test-output/manual-page-1.jpg`；文件大小 `165317` 字节，JPEG 尺寸 `1474×2159`，传输返回码 `6`（`TWRC_XFERDONE`），`pendingCount=0`，收到 `page_file_ready` 和 `scan_completed` 事件。成功记录见：[task-8-2026-07-21-rerun-success.json](实测记录/task-8-2026-07-21-rerun-success.json)。该结果证明 Task 8 单面 JPEG 文件传输闭环，不等同于 Task 14 的 300 页、双面、卡纸、断线和恢复验收通过。

## 4. 单面压力测试

```text
300张纸
ADF单面
300 DPI
JPEG
```

要求生成300个原图和300个缩略图，不丢页、不重复、顺序正确、数据库和文件数量一致。

## 5. 双面压力测试

```text
150张双面纸
ADF双面
300 DPI
JPEG
```

要求生成300个独立页面，正反面分别拥有 `pageId`，不合并、不丢面、不重复。

## 6. 连续补纸

ADF无纸后本轮正常结束，在同一任务继续扫描，新页面追加，已有 `pageId`不变，`sequence`继续递增。

## 7. 恢复

测试浏览器关闭、托盘退出、主进程终止、工作进程终止、Windows注销和重新登录。

要求已完成文件不丢失，任务可以恢复，中断任务不自动重新扫描，工作进程异常不导致HTTP主进程退出。

## 8. 异常

验证ADF无纸、卡纸、设备断开、设备占用、DSM缺失、文件传输不支持、JPEG不支持、磁盘空间不足和缩略图失败。

## 9. 稳定性

- 连续完成不少于3轮300页任务。
- 主进程内存不随累计页数线性增长。
- 任务结束后Data Source能够关闭。
- 扫描期间健康检查正常响应。
- 日志能够轮转。

## 10. 当前状态

2026-10-20 后续情况：上述六页成功任务再次开始扫描时，在 `DAT_IMAGEFILEXFER` 阶段收到 `TWCC_OPERATIONERROR=5`，参数设置与采集启用已成功，原有六页仍保留。用户随后确认“已经可以了”；此反馈不等同于已定位该错误根因或重复扫描稳定性验收通过。本轮 Git 收尾不追加真机请求，不改变下述历史成功证据，也不勾选 Task 14 完整验收。

2026-10-20 原生批量修复验证：实际任务 `task-aef80ef33ee5461b9f342b45f8971d2c` 以 ADF 单面、300 DPI 扫描，操作者确认本轮实际进纸 6 张，任务 `COMPLETED`、登记 6 页、无错误码。日志确认只启用一次 Data Source，连续传输 6 面，最后禁用一次；不是任务内逐页重新启用。6 个独立 pageId、序号 1–6，原图和缩略图均可完整解码且 GET 成功，原图 SHA-256、大小、尺寸与页面记录一致。

本次定位出跨任务首个 pageId 冲突，以及 i2000 在 `MSG_ENDXFER` 时短暂返回 `0x8001` 的兼容问题。后者在状态为成功、有待传输图像时，通过限时消息泵与结束确认取得标准 `TWRC_SUCCESS` 后继续，过程中不重新启用采集；本轮捕获 11 次中间返回、6 次标准结束成功，最后待传输数为 0。原始元数据与计数证据见 [i2000-native-batch-2026-10-20.json](实测记录/i2000-native-batch-2026-10-20.json)。临时逐页启停方案已移除，不算原生批量验收通过；本次六页验证也不替代双面、300 张单面、150 张双面及异常场景的正式验收。

已完成 `pytwain` 导入测试、真实 Data Source 枚举、Task 7.5 真实 Capability 只读冒烟探测、Task 8 单面 JPEG 文件传输闭环、便携式交付目录内容和运行时迁移检查，以及 API 和托盘专项自动化测试；客户机无 Python/Conda 的现场启动、驱动安装、300 页压力测试、双面及异常恢复验收、托盘真实 GUI 手工验收仍未完成。Task 7.5 和 Task 8 的逐项实测记录已保存；Task 14 正式真机全量验收仍未完成。

2026-10-20 增加一次开发机本地便携目录单页冒烟测试：使用目录内 `runtime\python.exe run.py` 直接启动，健康接口与设备枚举正常，ADF 单面 300 DPI 扫描一张测试纸后任务为 `COMPLETED`、页面数 1；原图和缩略图均可解码且 HTTP GET 返回 200，原图 SHA-256 与页面记录一致。详见 [task-13-2026-10-20-local-portable-single-page.json](实测记录/task-13-2026-10-20-local-portable-single-page.json)。本次没有验证报错的 `.cmd` 启动脚本、客户机无 Python/Conda 的部署、双面、长批次或异常场景，不能据此完成 Task 14 验收。
