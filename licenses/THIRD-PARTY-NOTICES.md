# 第三方组件说明

以下版本来自本次便携式运行时构建，完整版本以 `requirements.lock` 和 `runtime\Lib\site-packages` 下的发行元数据为准。

| 组件 | 版本 | 许可证/说明 |
| --- | --- | --- |
| pytwain | 2.3.0 | GPLv2；对应说明见 `GPL-2.0.txt` |
| FastAPI | 0.139.2 | MIT |
| Uvicorn | 0.51.0 | BSD-3-Clause |
| Pydantic | 2.13.4 | MIT |
| Pillow | 11.3.0 | HPND/PIL Software License |
| pystray | 0.19.5 | LGPLv3 |
| Starlette | 1.3.1 | BSD-3-Clause |
| AnyIO | 4.14.2 | MIT |
| httptools | 0.8.0 | MIT |
| watchfiles | 1.2.0 | MIT |
| websockets | 16.1.1 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT |
| python-dotenv | 1.2.2 | BSD-3-Clause |

本目录不擅自捆绑 KODAK 驱动或来源不明的 `TWAINDSM.DLL`。驱动和 TWAIN DSM 由客户机上的厂商驱动安装流程负责。

第三方组件的原始版权、许可证和源代码以其发行包内的 `dist-info` 元数据及项目官方发布内容为准。对外分发前必须由交付负责人完成许可证合规复核。
