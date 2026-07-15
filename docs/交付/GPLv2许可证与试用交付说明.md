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

- 包含GPLv2许可证文本。
- 包含pytwain原版权和许可证说明。
- 包含第三方组件清单。
- 记录项目对pytwain的修改。
- 明确完整对应源代码的提供方式。
- 完成项目方许可证合规确认。

未完成以上事项时不得对外分发安装包。

## 3. 建议目录

```text
LICENSES/
├─ GPL-2.0.txt
├─ pytwain-LICENSE.txt
└─ THIRD-PARTY-NOTICES.md

SOURCE/
├─ pytwain-2.3.0/
├─ pytwain项目内修改/
└─ 构建及依赖说明/
```

## 4. 修改记录

以下扩展可能涉及pytwain相关修改或派生封装，必须记录：

```text
MSG_QUERYSUPPORT
通用Capability容器设置
64位TWAINDSM兼容修复
TWSX_FILE直接JPEG流程
TWRC_CHECKSTATUS处理
```

## 5. 当前状态

目前只完成方案设计，尚未安装pytwain，也没有形成客户试用包。实际开发和交付时必须重新核对依赖版本、许可证文本和源代码范围。
