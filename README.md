# 档案本机扫描服务

本项目是在 Windows 客户机上运行的 Python 本机扫描程序，通过64位 TWAINDSM 调用 KODAK i2000系列 TWAIN Data Source，并向业务 Web 页面提供本机 HTTP接口。

## 当前状态

当前已完成工程初始化、配置目录与日志基础、SQLite 任务/页面持久化、进程间协议、工作进程生命周期管理，以及 DSM 加载和
TWAIN Data Source 枚举。项目环境为 `D:\archive-scan-service\.venv` 下的 Python 3.12.13 64 位 Conda 环境，解释器为
`D:\archive-scan-service\.venv\python.exe`。当前真机为 KODAK i2400，已枚举到 `KODAK Scanner: i2000`（x64）；本机
`kds_i2000.inf` 同时支持 i2400、i2420、i2600、i2620、i2800、i2820。Capability 编解码和规则已完成，真实 Capability
查询、文件传输、HTTP 接口、托盘程序和安装包仍未完成。

## 固定范围

```text
Windows 64位
Python 64位
pytwain 2.3.0
64位TWAINDSM.DLL
KODAK i2000系列 TWAIN Data Source（kds_i2000.inf）
FastAPI主进程 + TWAIN工作子进程
TWSX_FILE
每面独立JPEG
SQLite任务持久化
Pillow只生成缩略图
HTTP地址：http://127.0.0.1:17653
```

## 项目职责

负责：

- TWAIN设备枚举。
- 全部标准和私有 Capability查询与设置。
- 平板、ADF单面、ADF双面扫描。
- 单页JPEG原图保存。
- 缩略图生成。
- 多任务持久化和恢复。
- 页面读取和删除。
- FastAPI、WebSocket和托盘程序。

不负责：

- 档案业务页面。
- 业务系统登录和权限。
- 扫描文件上传业务后端。
- 正式档案原文挂接。
- 原图二次加工。

## 文档入口

- [完整设计方案](docs/设计/Windows%20Python本机扫描服务技术方案.md)
- [功能清单](docs/需求/功能清单.md)
- [技术选型决策](docs/决策记录/0001-技术选型与范围.md)
- [TWAIN开发规则](docs/规范/TWAIN开发规则.md)
- [API设计规范](docs/规范/API设计规范.md)
- [开发规范](docs/规范/开发规范.md)
- [KODAK i2000系列驱动测试与验收](docs/测试/KODAK%20i2000系列驱动测试与验收.md)
- [GPLv2许可证与试用交付说明](docs/交付/GPLv2许可证与试用交付说明.md)
- [详细实现计划](docs/superpowers/plans/2026-07-15-python扫描服务实现计划.md)

## 开发前提

开始开发前必须：

1. 阅读根目录 `AGENTS.md`。
2. 确认当前处于独立开发分支，不直接在 `master`开发。
3. 使用64位 Python环境。
4. 使用测试驱动开发。
5. 将真实设备测试与普通单元测试分离。

## 关联项目

Vue前端项目：

```text
D:\document-vue3
```

前端和本项目只通过：

```text
http://127.0.0.1:17653/api/v1
```

进行通信。
