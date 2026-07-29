# Task 13 便携式 Windows 交付目录设计

## 目标

生成 `D:\archive-scan-service\ArchiveScanService` 交付目录，使没有预装 Python、Conda 或 pip 的 Windows x64 客户机可以通过目录内脚本启动本机扫描服务，供客户现场测试。

## 方案决定

Task 13 第一阶段不使用 PyInstaller，也不要求客户机安装 Conda。构建机创建干净的 Python 3.12.13 x64 Conda 环境，使用非 editable 方式安装项目和生产依赖，再使用 `conda-pack` 生成可迁移运行时。交付目录包含解压后的 `runtime`、项目源代码、启动脚本、部署说明、故障排查和许可证文件。

当前开发环境 `D:\archive-scan-service\.venv` 只用于开发和测试，不直接复制；该环境存在 editable 项目安装，直接复制会继续引用开发机路径。

## 交付目录

```text
ArchiveScanService/
├─ runtime/                         # 完整 Python 运行时和生产依赖
├─ app/                             # 本项目源代码
├─ run.py                           # 托盘和本机 HTTP 服务入口
├─ 启动服务.cmd                     # 无需 Python 的正常启动脚本
├─ 启动服务-调试.cmd                # 保留控制台的诊断启动脚本
├─ 部署流程.md                      # 客户机安装、驱动、启动和验证流程
├─ 故障排查.md                      # 常见错误和日志位置
├─ 版本信息.txt                     # 构建时间、Python 和依赖版本
├─ requirements.lock                # 生产运行依赖锁定版本
├─ source-offer.txt                 # GPLv2 源码提供说明
└─ licenses/
   ├─ GPL-2.0.txt
   └─ THIRD-PARTY-NOTICES.md
```

## 运行边界

- 客户机不需要安装 Python、Conda、pip、FastAPI 或 Pillow。
- KODAK i2000 系列 64 位驱动和对应 TWAIN Data Source 仍必须单独安装；交付目录不擅自捆绑来源不明的 `TWAINDSM.DLL`。
- 扫描服务仍只监听 `http://127.0.0.1:17653`，运行方式仍是当前用户托盘程序，不注册 Windows Service。
- “开机启动”使用当前用户 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`；迁移目录后必须重新启用，避免注册表仍指向旧路径。
- `%LOCALAPPDATA%\ArchiveScanService` 是运行数据目录，默认不随交付目录复制历史数据。

## 验证要求

交付前必须在当前开发机验证：运行时没有 editable 安装标记、启动脚本使用目录内 Python、目录包含许可证和部署文档、目录不包含 `TWAINDSM.DLL`，并完成一次 `health` 接口启动验证。随后应在一台没有 Python/Conda 的干净 Windows x64 测试账户中验证驱动安装、托盘启动、`workerReady`、设备枚举和退出。

## 非目标

- 本阶段不制作 PyInstaller 单文件或 Inno Setup 安装包。
- 本阶段不自动安装 KODAK 驱动，不覆盖客户已有驱动，不迁移历史 SQLite 数据。
