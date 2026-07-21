# TWAIN开发规则

## 1. 进程归属

- DSM、Data Source和TWAIN消息循环只能存在于工作子进程。
- 主进程不得直接持有TWAIN对象。
- 工作子进程不得写SQLite。
- 工作进程内所有TWAIN调用严格串行。

## 2. 状态管理

TWAIN状态转换必须明确记录，不允许跨状态调用。打开DSM、打开Data Source、启用Data Source、传输、禁用和关闭必须成对处理。

异常路径也必须执行可用的关闭操作；驱动无响应时允许终止整个工作子进程，不在主进程中强行释放TWAIN句柄。

## 3. 设备枚举

- 只枚举64位 Data Source。
- 返回厂商、产品系列、产品名和协议版本。
- 不根据显示名称猜测设备能力。
- 默认设备不存在时返回明确状态，不自动选择其他设备。

## 4. Capability查询

- 先查询 `CAP_SUPPORTEDCAPS`。
- 对每个 Capability查询 `MSG_QUERYSUPPORT`。
- 按操作位执行可用的 `GET`、`GETCURRENT` 和 `GETDEFAULT`；操作位必须原样保存，但不能单独作为前端固定配置字段的显示或设置门槛。
- 支持全部标准容器和已确认Item类型。
- 解析失败时保留 Capability编号并返回该项错误，不中断全部查询。
- 私有 Capability必须同时记录设备厂商和产品名，不能只按编号映射。

## 5. Capability设置

- 只能设置本次设备会话查询到的 Capability。
- 设置前必须通过 Item 类型、枚举集合或范围边界校验；`TWQC_SET` 操作位作为查询结果保留，不作为固定配置设置的唯一拒绝条件。
- 枚举值必须来自驱动返回集合。
- 范围值必须符合边界和步长。
- 保留原始Item类型。
- `MSG_SET` 返回状态作为设置结果；设备支持 `MSG_GETCURRENT` 时必须回读，设备不支持时记录 `readbackUnavailable`。
- `TWRC_CHECKSTATUS`不能当作普通成功忽略，必须读取最终值。
- 标准参数按照来源、单双面、颜色位深、纸张、分辨率、JPEG质量、增强、私有参数的顺序设置。

## 6. 文件传输

- 固定 `ICAP_XFERMECH = TWSX_FILE`。
- 固定 `ICAP_IMAGEFILEFORMAT = TWFF_JFIF`。
- 每次传输前生成唯一临时路径。
- Data Source完成写入后再原子重命名。
- 文件传输失败时不得创建页面数据库记录。
- 不调用会先输出BMP再由Pillow转换的高级接口。

## 7. 扫描模式

- 平板：`CAP_FEEDERENABLED = FALSE`。
- ADF单面：启用Feeder，关闭Duplex，启用AutoFeed。
- ADF双面：启用Feeder和Duplex，启用AutoFeed。
- 模式可用性以驱动实际查询结果为准。
- ADF无纸属于正常结束；卡纸和离线属于异常。

## 8. 原图保护

- 原始JPEG由驱动直接产生。
- 不使用Pillow或其他库重新保存原图。
- 原图写入后计算SHA-256。
- 缩略图操作不得改变原图摘要。
