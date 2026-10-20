"""TWAIN 失败的安全诊断字段，不携带文件路径或异常堆栈。"""

from collections.abc import Mapping
import re
from typing import Any


STAGES = {
    "query_capabilities": "查询设备能力",
    "set_capability": "设置设备参数",
    "prepare_transfer": "准备文件传输",
    "enable_source": "启动设备采集",
    "wait_event": "等待图像就绪",
    "read_extended_info": "读取扩展图像信息",
    "read_image_info": "读取图像信息",
    "transfer_file": "传输图像文件",
    "setup_file_transfer": "设置图像输出文件",
    "image_file_transfer": "从驱动传输图像",
    "end_transfer": "结束单面图像传输",
    "reset_transfers": "复位待传输队列",
    "disable_source": "禁用设备采集",
    "close_source": "关闭设备数据源",
    "scan": "扫描处理",
}


def safe_diagnostics(value: Any) -> dict[str, Any]:
    """只保留明确允许公开的标识、编号和异常类型。"""

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    if isinstance(value.get("stage"), str) and value["stage"] in STAGES:
        result["stage"] = value["stage"]
    for key in (
        "capabilityId",
        "twainConditionCode",
        "twainReturnCode",
        "pendingCount",
    ):
        item = value.get(key)
        if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 65535:
            result[key] = item
    exception_type = value.get("exceptionType")
    if isinstance(exception_type, str) and re.fullmatch(
        r"[A-Za-z_][A-Za-z_0-9]{0,63}", exception_type
    ):
        result["exceptionType"] = exception_type
    return result


def describe_failure(
    error: BaseException, stage: str, capability_id: int | None = None
) -> dict[str, Any]:
    """从工作进程内的异常链提取原始 TWAIN 异常类别和条件码。"""

    import twain

    root = error
    seen = set()
    nested: dict[str, Any] = {}
    while id(root) not in seen:
        nested.update(safe_diagnostics(getattr(root, "diagnostics", None)))
        seen.add(id(root))
        if root.__cause__ is None:
            break
        root = root.__cause__
    result: dict[str, Any] = {"stage": stage, "exceptionType": type(root).__name__}
    result.update(nested)
    result["exceptionType"] = type(root).__name__
    if capability_id is not None:
        result["capabilityId"] = capability_id
    if isinstance(root, twain.exceptions.TwainError):
        for code, exception_type in twain._exc_mapping.items():
            if type(root) is exception_type:
                result["twainConditionCode"] = code
                break
    if isinstance(root, RuntimeError):
        match = re.fullmatch(r"Unexpected result:\s*(\d+)", str(root))
        if match:
            result["twainReturnCode"] = int(match.group(1))
    return safe_diagnostics(result)


def failure_message(error_code: str, diagnostics: Any) -> str:
    """用受控字段生成可保存到任务记录和显示在页面上的中文说明。"""

    detail = safe_diagnostics(diagnostics)
    if "capabilityId" in detail:
        message = f"TWAIN 参数 0x{detail['capabilityId']:04X} 设置失败"
    elif detail.get("stage") in STAGES:
        message = f"扫描失败：{STAGES[detail['stage']]}"
    else:
        message = (
            "TWAIN 参数设置失败"
            if error_code == "TWAIN_CAPABILITY_SET_FAILED"
            else "扫描失败"
        )
    if "twainConditionCode" in detail:
        message += f"（驱动条件码 TWCC={detail['twainConditionCode']}）"
    if "twainReturnCode" in detail:
        message += f"（驱动返回码 TWRC={detail['twainReturnCode']}）"
    return message
