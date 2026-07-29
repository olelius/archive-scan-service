# GPLv2许可证与试用交付说明

## 1. 适用组件

```text
组件：pytwain
版本：2.3.0
许可证：GPLv2
```

客户试用属于向第三方分发，不能因为免费、临时、未验收或限定期限而省略许可证义务。

## 2. 交付前门禁

试用和正式安装包交付前必须：

- 包含 GPLv2 许可证文本。
- 包含 pytwain 原版权和许可证说明。
- 包含第三方组件清单。
- 记录项目对 pytwain 的修改。
- 明确完整对应源代码的提供方式。
- 完成项目方许可证合规确认。

未完成以上事项时不得对外分发交付包。

## 3. 当前便携式测试交付目录

```text
ArchiveScanService/
├─ runtime/                         # Python 3.12.13 x64 和生产依赖
├─ app/                             # 本项目源码
├─ run.py
├─ 启动服务.cmd
├─ 启动服务-调试.cmd
├─ 部署流程.md
├─ 故障排查.md
├─ requirements.lock
├─ source-offer.txt
└─ licenses/
   ├─ GPL-2.0.txt
   └─ THIRD-PARTY-NOTICES.md
```

当前目录是无需客户机安装 Python/Conda 的便携式测试交付。运行时由干净的 Python 3.12.13 x64 Conda 环境构建并经 `conda-pack` 处理，项目以非 editable 方式安装；客户机不需要安装 Conda。目录复制到最终路径后，按《部署流程.md》先执行目录内的 `runtime\Scripts\conda-unpack.exe`。

## 4. 修改记录

以下扩展可能涉及 pytwain 相关修改或派生封装，必须记录：

```text
MSG_QUERYSUPPORT
通用 Capability 容器设置
64 位 TWAINDSM 兼容修复
TWSX_FILE 直接 JPEG 流程
TWRC_CHECKSTATUS 处理
```

## 5. 驱动和运行时边界

- KODAK i2000 系列 64 位驱动和 TWAIN Data Source 必须在客户机单独安装。
- 本交付目录不擅自捆绑来源不明的 `TWAINDSM.DLL`，也不替代厂商驱动安装。
- 运行数据默认位于 `%LOCALAPPDATA%\ArchiveScanService`，不随便复制历史数据。
- 服务仍只监听 `http://127.0.0.1:17653`，运行方式仍是当前用户托盘程序。

## 6. 当前状态

已生成 `D:\archive-scan-service\ArchiveScanService` 便携式测试目录，并完成目录内容、运行时导入、开发机路径隔离和复制到新路径后的 `conda-unpack`/运行时导入检查。该目录用于客户机测试；Task 14 正式真机验收和 PyInstaller/Inno Setup 正式安装包不在本次交付范围内。
