# Task 10 任务生命周期与重启恢复设计

## 目标

为扫描服务提供可持久化的任务状态机、单活动扫描互斥和服务重启恢复，保证历史页面可继续查看，且服务重启不会自动重新驱动扫描仪。

## 现有边界

- `TaskRepository` 是主进程 SQLite 写入入口；Worker 不访问数据库。
- `TaskStatus` 已包含 `CREATED`、`SCANNING`、`STOPPING`、`STOPPED`、`COMPLETED`、`FAILED` 和 `CANCELLED`。
- 页面登记由 `PageService` 完成；恢复逻辑不得删除原图、缩略图或页面记录。
- Worker 已能发布 `scan_stopped`、`scan_completed`、`scan_failed`，Task 10 只提供主进程状态编排接口，不把 TWAIN 对象引入服务层。

## 方案

### TaskService

`TaskService` 使用模块级 `RLock` 覆盖所有实例，串行化同一主进程内的状态变更。`start_scan()` 先校验任务是否存在和当前状态，再调用仓储的数据库事务抢占方法；仓储在 `BEGIN IMMEDIATE` 中同时检查 `SCANNING/STOPPING` 活动任务并更新目标任务，防止不同服务实例绕过进程锁产生第二个活动扫描。

允许的主要转换如下：

```text
CREATED   -> SCANNING, CANCELLED
SCANNING  -> STOPPING, COMPLETED, FAILED
STOPPING  -> STOPPED, FAILED
STOPPED   -> SCANNING, CANCELLED
FAILED    -> SCANNING, CANCELLED
COMPLETED -> SCANNING, CANCELLED
CANCELLED -> （终态）
```

`stop_scan()` 只将 `SCANNING` 置为 `STOPPING`；Worker 停止事件由 `mark_stopped()` 置为 `STOPPED`。完成和失败分别由 `complete_scan()`、`fail_scan()` 记录。失败状态保留稳定错误码和面向调用方的错误消息；重新开始扫描时清除上次错误并保存本轮参数快照。

当前 IPC 协议没有取消命令，因此活动任务不能直接转换为 `CANCELLED`；只有尚未开始的
`CREATED` 任务允许显式取消。活动任务必须先请求停止并收到 Worker 的停止确认，避免
数据库状态与实际扫描设备状态脱节。

### RecoveryService

`recover()` 在服务启动阶段扫描全部任务。对 `SCANNING` 或 `STOPPING` 任务执行一次事务更新为 `STOPPED`，写入 `SERVICE_RESTARTED` 和固定中文说明；其他任务原样返回。该服务不创建新 Worker 命令、不扫描设备、不删除任何页面或文件，因此恢复后的历史页面和 sequence 保持不变。

## 错误边界

- 任务不存在：`TaskNotFoundError` / `TASK_NOT_FOUND`。
- 非法状态转换：`TaskStateError` / `TASK_STATE_INVALID`。
- 已有其他活动任务：`ScannerBusyError` / `SCANNER_BUSY`，同时保留活动任务标识供上层记录。
- 重复创建：`TaskAlreadyExistsError`，不覆盖已有任务。

## 验证设计

- 单元测试覆盖合法状态转换、非法转换、错误码、参数快照、重复创建和同进程多实例互斥。
- 集成测试先写入包含页面的 `SCANNING/STOPPING` 任务，再执行恢复，验证状态、`SERVICE_RESTARTED`、页面记录和文件均保留，并验证恢复不会向 Worker 发送命令。
- 在实现后运行 Task 10 专项测试、全量 pytest、Ruff、Python 编译检查和 `git diff --check`。
