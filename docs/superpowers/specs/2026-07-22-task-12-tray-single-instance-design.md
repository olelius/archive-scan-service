# Task 12：托盘程序和单实例运行设计

## 1. 目标

将当前可由 `run.py` 启动的 FastAPI/Worker 进程包装为登录用户级 Windows 托盘程序，保证同一用户会话中只有一个本机扫描服务实例，并提供安全、可验证的有序退出流程。

## 2. 范围

本 Task 实现以下行为：

- 使用 Windows 当前用户命名空间中的命名互斥体保证单实例。
- 第二个实例在启动 HTTP 服务、Worker 和托盘图标前被拒绝。
- 使用 `pystray` 创建托盘图标和菜单。
- 菜单提供“服务状态”“扫描仪状态”“扫描仪”“厂商”“Worker”五项只读状态，以及动态“关闭服务/开启服务”、可勾选“开机启动”、“打开数据目录”和“退出”四个控制项。
- HTTP 服务继续固定监听 `127.0.0.1:17653`，不注册 Windows Service。
- 退出时先请求 Uvicorn 停止接收新连接，等待其完成关闭流程，再复用现有 FastAPI lifespan 关闭 Worker、事件中心和 SQLite，最后释放互斥体。
- `run.py` 作为正式入口转到托盘应用；测试仍可直接使用 `create_app()` 和 `ApplicationContext`。

日志目录、重启扫描组件和完整本机任务管理窗口仍不在本 Task 实现；开机启动和服务启停通过托盘控制项提供。

## 3. 组件设计

### 3.1 `SingleInstanceGuard`

`SingleInstanceGuard` 封装 Windows `CreateMutexW`、`GetLastError`、`ReleaseMutex` 和 `CloseHandle` 调用。默认互斥体名称为 `Local\\ArchiveScanService`，句柄在进程生命周期内保持打开。

- 首个实例创建并持有互斥体，`acquire()` 返回 `True`。
- 已有实例时 `CreateMutexW` 返回已有对象句柄并报告 `ERROR_ALREADY_EXISTS`；新实例关闭该句柄，`acquire()` 返回 `False`。
- `release()` 幂等，只释放首个实例实际持有的句柄。
- 底层 Win32 API 通过小型可注入后端隔离，单元测试不需要启动真实扫描服务，也能覆盖重复获取和释放语义。

### 3.2 `TrayApplication`

`TrayApplication` 负责组装单实例守卫、Uvicorn Server、FastAPI 应用上下文和 `pystray.Icon`，并允许测试注入应用工厂、Server、Icon、互斥体、启动项管理器和目录打开器。

启动顺序：

1. 获取命名互斥体；失败立即返回，不构造 Worker、Server 或 Icon。
2. 延迟导入并创建现有 FastAPI 应用，使用 `Settings.host`、`Settings.port` 和 `Settings.log_level` 构造 Uvicorn Server。
3. 在后台线程运行 Uvicorn，托盘 `Icon.run()` 保持在主线程。
4. 托盘状态菜单项从当前 ApplicationContext/Worker 状态读取就绪状态和 PID，并从设备枚举结果读取扫描仪在线状态、`productName` 和 `manufacturer`；设备查询短暂失败时保留最近一次已知设备信息。
5. Windows `pystray` 原生菜单不会在右键显示时自动重建；托盘注册 `GUID_DEVINTERFACE_IMAGE` 的 `WM_DEVICECHANGE` 通知，在收到图像设备到达、移除或设备节点变化时调用 `icon.update_menu()`，使外部扫描仪重启后能够重新读取状态。通知注册失败只记录警告，不阻止托盘服务启动。
6. `ApplicationContext.list_devices()` 在 TWAIN 枚举结果上合并 Windows SetupAPI/CfgMgr32 的 Image 类 PnP 状态；设备在场且配置正常时显示在线，phantom/缺失/配置异常时显示离线。PnP 查询失败只保留原有枚举状态，不阻断服务。
7. “开机启动”使用当前用户 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，启用时写入 `ArchiveScanService`，源码运行写入 Python 和 `run.py` 的 Windows 命令行，PyInstaller 运行写入当前可执行文件；取消勾选删除该值，不需要管理员权限。

目录菜单项使用 `os.startfile(str(settings.data_root))`，不拼接 shell 命令；测试通过注入 opener 检查传入的绝对数据目录。

### 3.3 服务控制

托盘中的服务控制项只停止或启动 HTTP 服务及其 FastAPI lifespan，不退出托盘，也不释放单实例互斥体：

- 服务线程存在且未请求退出时显示“关闭服务”。
- 服务停止、线程异常退出或尚未启动时显示“开启服务”；服务已明确停止时状态项显示“服务状态：未启动”。
- 关闭服务时先设置 `server.should_exit=True`，等待 Uvicorn 线程结束，再调用当前 ApplicationContext 的幂等 `close()`，清理 Worker、事件中心和 SQLite。
- 开启服务时重新创建 FastAPI 应用上下文和 Uvicorn Server，避免复用已经关闭的数据库和 Worker；等待 Uvicorn `started=True`（FastAPI lifespan 和 Worker 已完成）后调用 `icon.update_menu()`。
- 服务控制操作失败只记录日志并刷新菜单，不关闭托盘；服务手动停止期间，Uvicorn 线程结束不能触发托盘退出。

### 3.4 有序退出

“退出”菜单回调和 Server 异常收尾都进入同一个幂等关闭方法：

1. 标记关闭已开始，避免重复退出路径并停止托盘图标。
2. 将 `server.should_exit` 设为 `True`，让 Uvicorn 停止接受新请求并等待已有服务关闭。
3. 等待 Server 线程结束；超时则设置 `force_exit` 并再次等待。
4. 如果 Server 线程在两次等待后仍存活，记录稳定错误并保留 ApplicationContext 资源和命名互斥体，避免残留线程继续运行时第二实例抢占端口或共享资源；正常进程入口随后退出，由 Windows 回收进程资源。
5. Server 线程已结束时，显式调用 ApplicationContext 的幂等 `close()` 作为未完成 lifespan 或测试替身场景的兜底；真实 FastAPI lifespan 已调用时不会重复关闭资源。
6. 释放命名互斥体。

ApplicationContext 现有 `close()` 会按 WorkerSupervisor、EventHub、SQLite 的顺序清理资源；WorkerSupervisor 已具备优雅 shutdown 和超时强制回收能力，因此托盘层不直接接触 TWAIN DSM、Data Source 或数据库。

## 4. 错误处理

- 互斥体创建失败抛出清晰的本地启动错误，不返回 Python 堆栈给 HTTP 调用方。
- Server 启动异常记录日志并触发同一关闭流程，确保 Worker、数据库和互斥体不遗留。
- 数据目录打开失败只记录日志，不改变服务运行状态。
- 所有关闭路径幂等，重复调用不重复释放句柄、关闭队列或关闭数据库。

## 5. 测试设计

`tests/unit/test_tray_application.py` 覆盖：

- 首个实例获取成功、第二个实例被拒绝、释放后可再次获取。
- 第二个实例被拒绝时不启动 Server、Worker 或 Icon。
- 菜单包含五项只读状态、动态服务控制项和开机启动复选项，状态文本能反映 Worker、扫描仪在线状态、设备名称和厂商。
- 开机启动缺失时复选框未勾选，启用时写入正确的当前用户注册表命令，取消时删除启动值；注册表异常不阻断托盘。
- 服务控制项在开启、关闭和重新开启后分别显示“关闭服务”“开启服务”“关闭服务”，关闭服务不退出托盘，重新开启创建新的应用上下文。
- Windows 原生菜单在收到 `WM_DEVICECHANGE` 后能够显示最新状态，关闭托盘时设备通知窗口能够注销并停止。
- Windows PnP `Present=False` 时设备列表和托盘“扫描仪状态”显示离线；PnP `Present=True` 且配置正常时显示在线。
- “打开数据目录”向 opener 传递 `Settings.data_root`。
- 退出先请求 Server 停止并等待，再关闭 ApplicationContext，最后释放互斥体；重复退出只执行一次。
- Uvicorn 线程以 `SystemExit` 失败和强制退出超时均不会假报成功或提前释放单实例句柄。

测试使用真实业务接口的最小替身，不加载真实 TWAIN DSM，不启动真实 GUI；现有全量测试继续作为回归验证。
