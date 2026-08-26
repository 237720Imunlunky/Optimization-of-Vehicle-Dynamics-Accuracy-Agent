"""把通过安全校验的参数集转换为独立CarSim展开模型。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from run_coast_parameter_scan import replace_all_scalar
from run_parameter_sensitivity import format_value, replace_keyword
from run_powertrain_model_correction import scale_battery_resistance_carpet


BASELINE_AERO_AREA_M2 = 2.2
REGISTRY_PATH = Path(__file__).resolve().parent / "config" / "parameter_registry.json"


def load_registry() -> dict[str, Any]:
    """读取机器可执行的参数注册表，避免模型写入逻辑重复维护参数清单。"""
    import json

    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def scale_motor_speed_regions(text: str, low_scale: float, high_scale: float) -> str:
    """按转速区间缩放两张电机外特性表，转速轴和表结构保持不变。"""
    pattern = re.compile(r"(?ms)(MMOTOR_MAX_TABLE\s+LINEAR\s*\n)(.*?)(\nENDTABLE)")

    def update(match: re.Match[str]) -> str:
        output = []
        changed = 0
        for line in match.group(2).splitlines():
            if "," not in line or line.lstrip().startswith("!"):
                output.append(line)
                continue
            left, right = line.split(",", 1)
            try:
                rpm = float(left.strip())
                torque = float(right.strip())
            except ValueError:
                output.append(line)
                continue
            scale = low_scale if rpm <= 6000.0 else high_scale
            output.append(f"{format_value(rpm)}, {format_value(torque * scale)}")
            changed += 1
        if changed == 0:
            raise ValueError("电机扭矩表中没有可缩放的数值行")
        return match.group(1) + "\n".join(output) + match.group(3)

    updated, count = pattern.subn(update, text)
    if count != 2:
        raise ValueError(f"预期缩放2张电机扭矩表，实际为{count}张")
    return updated


def set_drive_power_limits(text: str, power_kw: float) -> str:
    """同步修改三个驱动功率字段，避免功率管理定义相互矛盾。"""
    for keyword in ("PWR_HEV_DRV_MAX", "PWR_EV_MODE", "PWR_DRV_THROTTLE_COEFFICIENT"):
        text = replace_keyword(text, keyword, power_kw)
    return text


def apply_registered_binding(text: str, value: float, binding: dict[str, Any]) -> str:
    """按照注册表中的绑定类型写入一个参数，新增参数只需补充注册表。"""
    binding_type = binding.get("type")
    keywords = [str(keyword) for keyword in binding.get("keywords", [])]
    if binding_type == "scalar_group":
        target_value = float(value) * float(binding.get("base_value", 1.0))
        expected_count = int(binding.get("expected_count", 1))
        for keyword in keywords:
            if expected_count == 1:
                text = replace_keyword(text, keyword, target_value)
            else:
                text, count = replace_all_scalar(text, keyword, target_value)
                if count != expected_count:
                    raise ValueError(f"{keyword}预期修改{expected_count}处，实际为{count}处")
        return text
    if binding_type == "carpet_scale":
        for keyword in keywords:
            text = scale_battery_resistance_carpet(text, keyword, float(value))
        return text
    raise ValueError(f"参数注册表中的绑定类型暂不支持：{binding_type}")


def apply_parameter_set(
    text: str, parameters: dict[str, float], registry: dict[str, Any] | None = None,
) -> str:
    """从注册表批量生成完整候选，所有倍率都相对正式基线而非上轮结果。"""
    if registry is None:
        registry = load_registry()
    specs = registry["parameters"]
    low_name, high_name = "motor_low_speed_torque_scale", "motor_high_speed_power_scale"
    if low_name in parameters and high_name in parameters:
        text = scale_motor_speed_regions(text, float(parameters[low_name]), float(parameters[high_name]))
    for name, value in parameters.items():
        if name in {low_name, high_name}:
            continue
        spec = specs.get(name)
        if spec is None:
            raise ValueError(f"参数注册表中不存在：{name}")
        binding = spec.get("binding")
        if not binding:
            raise ValueError(f"参数{name}缺少可执行CarSim绑定定义")
        text = apply_registered_binding(text, float(value), binding)
    return text


def build_candidate_template(baseline: Path, destination: Path, parameters: dict[str, float]) -> str:
    """创建ASCII候选模板并返回SHA-256，绝不覆盖正式基线文件。"""
    source = baseline.read_bytes().decode("ascii")
    candidate = apply_parameter_set(source, parameters)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = candidate.encode("ascii")
    destination.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def audit_parameter_bindings(text: str, parameters: dict[str, float]) -> dict[str, Any]:
    """复核关键标量确实写入模型，表格参数由数量检查和哈希负责追溯。"""
    scalar_expectations = {
        "M_SU": float(parameters["vehicle_mass_kg"]),
        "AREA_AERO": BASELINE_AERO_AREA_M2 * float(parameters["aero_area_scale"]),
        "PWR_HEV_DRV_MAX": float(parameters["drive_power_limit_kw"]),
        "PWR_EV_MODE": float(parameters["drive_power_limit_kw"]),
        "PWR_DRV_THROTTLE_COEFFICIENT": float(parameters["drive_power_limit_kw"]),
    }
    checks = {}
    for keyword, expected in scalar_expectations.items():
        match = re.search(rf"(?m)^\s*{re.escape(keyword)}\s+([-+0-9.eE]+)", text)
        actual = float(match.group(1)) if match else None
        checks[keyword] = {"expected": expected, "actual": actual, "passed": actual is not None and abs(actual - expected) <= 1e-9}
    rr_values = [float(match.group(1)) for match in re.finditer(r"(?m)^\s*RR_C\s+([-+0-9.eE]+)", text)]
    checks["RR_C_all_wheels"] = {
        "expected": float(parameters["rr_c"]),
        "actual": rr_values,
        "passed": len(rr_values) == 4 and all(abs(value - float(parameters["rr_c"])) <= 1e-12 for value in rr_values),
    }
    return {"passed": all(item["passed"] for item in checks.values()), "checks": checks}
