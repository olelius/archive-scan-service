# KODAK i2000系列驱动测试与验收

## 1. 环境

```text
Windows 64位
Python 64位打包程序
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

验证查询、容器、Item类型、允许值、当前值、默认值、设置结果和最终生效值。

### 3.1 Task 7.5 真实 Capability 冒烟探测

Task 7.5 是 Task 8 文件传输开发前的只读真机前置关卡，不替代 Task 14 正式全量验收。必须在实际连接 KODAK i2400、使用 `kds_i2000.inf` 和 64 位 `KODAK Scanner: i2000` Data Source 的环境中执行。

执行步骤：

1. 在 TWAIN 工作进程内以 `show_ui=False` 打开目标 Data Source，确认 DSM、Data Source 和进程均为 64 位。
2. 读取 `CAP_SUPPORTEDCAPS`，确认返回的 Capability 列表可遍历。
3. 对每个 Capability 调用 `MSG_QUERYSUPPORT`，记录 `TWQC_GET`、`TWQC_GETCURRENT`、`TWQC_GETDEFAULT` 和 `TWQC_SET` 操作位。
4. 按支持位读取当前值和默认值，保留 Capability 编号、标准/私有标识、容器类型、原始 Item 类型、原始值及单项 `queryError`。
5. 关闭 Data Source，确认查询过程无崩溃、挂起、厂商界面弹出或资源未释放。

本步骤只做 Capability 只读查询，不执行任意 Capability 设置、不启动扫描、不验证 JPEG 文件传输。未设置 `RUN_TWAIN_CAPABILITY_MANUAL=1` 时的自动跳过不能记为通过；真实输出必须保存 DSM 路径、Data Source 身份、驱动入口、Capability 数量和逐项查询结果。探测失败时保留原始结果并暂停 Task 8，不得标记为驱动兼容通过。

### 3.2 Task 7.5 实测结果（2026-07-20）

- Python：`D:\archive-scan-service\.venv\python.exe`，Python 3.12.13，x64。
- DSM：`C:\Windows\System32\TWAINDSM.dll`，x64。
- Data Source：`Eastman Kodak / Document Imaging / KODAK Scanner: i2000`，TWAIN 协议 2.4，驱动信息 `KDS v16.1 2018/2/7`。
- 驱动入口：`kds_i2000.inf`；当前物理设备记录为 KODAK i2400，Windows PnP 名称显示为 `KODAK i2800 Scanner`。
- 工作进程以 `show_ui=False` 打开 Data Source，完成 Capability 查询后正常关闭；未执行 Capability 设置、图像扫描或文件传输。
- `CAP_SUPPORTEDCAPS` 返回并遍历 156 项：`TW_ONEVALUE` 46 项、`TW_ENUMERATION` 79 项、`TW_RANGE` 22 项、`TW_ARRAY` 9 项；记录了每项编号、操作位、容器类型、Item 类型、当前值、默认值和 `queryError`。
- 其中 33 项由驱动返回 `BadCapability`，均保留为逐项 `queryError`，没有中断其他 Capability 查询；该结果不等同于设置兼容性或正式扫描验收通过。
- 逐项记录：[task-7.5-2026-07-20.json](实测记录/task-7.5-2026-07-20.json)。

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

已完成 `pytwain` 导入测试、真实 Data Source 枚举和 Task 7.5 真实 Capability 只读冒烟探测；Task 8 文件传输测试、300 页压力测试和打包测试尚未完成。Task 7.5 的 156 项逐项记录已保存，允许进入 Task 8；Task 14 正式真机全量验收仍未完成。
