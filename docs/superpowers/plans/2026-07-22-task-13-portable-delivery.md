# Task 13 便携式交付目录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `D:\archive-scan-service\ArchiveScanService` 生成不依赖客户机 Python/Conda 安装的 Windows x64 便携式交付目录。

**Architecture:** 构建机创建干净的 Python 3.12.13 x64 环境，非 editable 安装项目和生产依赖后使用 `conda-pack` 处理环境前缀。交付目录携带 `runtime`、项目源代码、启动脚本、部署说明、故障排查、依赖锁定和 GPLv2 交付文件；KODAK 驱动单独安装。

**Tech Stack:** Python 3.12.13 x64、Conda/conda-pack、PowerShell、FastAPI、pytwain 2.3.0、Windows TWAIN。

---

### Task 1：固定交付目录契约

**Files:**
- Create: `tests/integration/test_distribution_contents.py`
- Create: `docs/superpowers/specs/2026-07-22-task-13-portable-delivery-design.md`

- [x] **Step 1：写入交付目录测试**

测试检查 `ArchiveScanService` 必须包含 `runtime/python.exe`、`runtime/pythonw.exe`、`app`、`run.py`、两个启动脚本、部署文档、故障排查、`requirements.lock`、GPLv2 文件和第三方说明；同时拒绝 editable `.pth`、绝对开发机路径和 `TWAINDSM.DLL`。

- [x] **Step 2：运行测试确认交付目录尚不存在**

Run: `D:\archive-scan-service\.venv\python.exe -m pytest tests/integration/test_distribution_contents.py -v`

Expected: 因 `ArchiveScanService` 尚未生成而失败。

### Task 2：编写目录内启动与部署说明

**Files:**
- Create: `ArchiveScanService/启动服务.cmd`
- Create: `ArchiveScanService/启动服务-调试.cmd`
- Create: `ArchiveScanService/部署流程.md`
- Create: `ArchiveScanService/故障排查.md`
- Create: `ArchiveScanService/版本信息.txt`
- Create: `ArchiveScanService/source-offer.txt`

- [x] **Step 1：正常启动脚本使用目录内 `runtime/pythonw.exe`**
- [x] **Step 2：调试启动脚本使用目录内 `runtime/python.exe` 并保留控制台**
- [x] **Step 3：部署文档说明驱动安装、复制、`conda-unpack`、启动、健康检查、开机启动和卸载**
- [x] **Step 4：故障文档说明端口、驱动、日志、数据目录和 Worker 未就绪排查**

### Task 3：构建干净运行时和许可证内容

**Files:**
- Create: `ArchiveScanService/runtime/`
- Create: `ArchiveScanService/app/`
- Create: `ArchiveScanService/run.py`
- Create: `ArchiveScanService/requirements.lock`
- Create: `ArchiveScanService/licenses/GPL-2.0.txt`
- Create: `ArchiveScanService/licenses/THIRD-PARTY-NOTICES.md`

- [x] **Step 1：在 `build/task13` 创建 Python 3.12.13 x64 干净环境**
- [x] **Step 2：非 editable 安装项目和生产依赖，排除 pytest、ruff、pyinstaller 等开发依赖**
- [x] **Step 3：使用 `conda-pack` 构建可迁移运行时并解压到 `ArchiveScanService/runtime`**
- [x] **Step 4：复制 `app` 和 `run.py`，不复制开发机 editable `.pth` 或测试数据**
- [x] **Step 5：生成依赖锁定和第三方许可证索引**

实际结果：构建环境位于 `build/task13/runtime-env`，运行时压缩包为 `build/task13/ArchiveScanRuntime.zip`；交付目录已包含 Python 3.12.13 x64、生产依赖、源码、启动脚本、中文部署/故障文档和 GPLv2 材料。运行时构建历史中的开发机路径已移除。

### Task 4：交付目录验证

**Files:**
- Modify: `tests/integration/test_distribution_contents.py`

- [x] **Step 1：运行交付目录测试**

Run: `D:\archive-scan-service\.venv\python.exe -m pytest tests/integration/test_distribution_contents.py -v`

Expected: 所有目录、脚本、文档和许可证检查通过。

- [x] **Step 2：验证打包运行时自身不包含 editable 标记**

Run: `D:\archive-scan-service\ArchiveScanService\runtime\python.exe -c "import sys; print(sys.version); import fastapi, twain, PIL, pystray; print('runtime imports ok')"`

实际结果：目录内运行时可导入 `fastapi`、`twain`、`PIL` 和 `pystray`，`sys.prefix` 指向交付目录，未发现开发机 editable 路径。

迁移复核：从原始 `ArchiveScanRuntime.zip` 解出运行时，在新路径执行 `conda-unpack.exe` 后，`fastapi`、`twain`、`PIL`、`pystray` 导入通过，`sys.prefix` 和模块路径均指向新路径。

- [ ] **Step 3：在不干扰现有服务的前提下启动交付目录并检查 `http://127.0.0.1:17653/api/v1/health`**

当前固定端口 `127.0.0.1:17653` 的监听进程已核对为 `D:\archive-scan-service\.venv\python.exe`（开发环境），本次不停止该进程，也不把它的健康响应冒充成交付目录验证；客户机部署后按《部署流程.md》执行交付目录健康检查。

- [x] **Step 4：保留交付目录供客户复制**

本次没有启动独立交付验证进程，因此不执行停止操作；交付目录保留在 `D:\archive-scan-service\ArchiveScanService`。

### Task 5：同步项目文档

**Files:**
- Modify: `README.md`
- Modify: `docs/交付/GPLv2许可证与试用交付说明.md`
- Modify: `docs/superpowers/plans/2026-07-15-python扫描服务实现计划.md`
- Modify: `docs/设计/Windows Python本机扫描服务技术方案.md`
- Modify: `docs/测试/KODAK i2000系列驱动测试与验收.md`

- [x] **Step 1：README 说明当前交付方式为便携式目录，不要求客户安装 Python/Conda**
- [x] **Step 2：GPLv2 文档说明目录交付、源码、许可证和 KODAK 驱动的边界**
- [x] **Step 3：原 Task 13 计划从 PyInstaller/Inno Setup 改为便携式交付目录，并记录验证结果**
- [x] **Step 4：运行受影响测试、Ruff、compileall 和 `git diff --check`**

实际结果：交付专项测试 `3 passed`；`ruff check app tests` 输出 `All checks passed!`；`compileall app tests` 和 `git diff --check` 均退出码 0。未重复运行未改动业务代码的全量测试。客户机无 Python/Conda 的真实部署验证仍待执行。
