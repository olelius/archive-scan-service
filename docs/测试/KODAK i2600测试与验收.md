# KODAK i2600测试与验收

## 1. 环境

```text
Windows 64位
Python 64位打包程序
64位TWAINDSM.DLL
KODAK i2600 64位TWAIN Data Source
本机SSD
```

## 2. 设备

- 能发现KODAK i2600。
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

尚未完成 `pytwain`导入测试、真实设备枚举、Capability测试、文件传输测试、300页压力测试和打包测试。任何一项未实际执行前不得标记通过。
