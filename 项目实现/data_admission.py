"""实车数据准入流水线：自动检查、分类副本、人工复核和可追溯清单。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from audit_coasting_can_control import (
    DRIVE_MODE_NAMES,
    GEAR_NAMES,
    category_summary,
    decode_selected_signals,
    numeric_summary,
    window_values,
)
from config_loader import load_project_config
from decode_blf import load_dependencies
from runtime_paths import PROJECT_ROOT, load_runtime_paths


REGISTRY_PATH = PROJECT_ROOT / "conditions" / "condition_registry.json"
REVIEWS_PATH = PROJECT_ROOT / "config" / "admission_reviews.local.json"
STATUS_FOLDERS = {"accepted": "合格", "rejected": "不合格", "pending_review": "待人工复核"}


def read_json(path: Path, default: Any = None) -> Any:
    """读取UTF-8 JSON；可选文件不存在时返回默认值。"""
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    """原子写入结构化结果，避免中断留下半截清单。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    """分块计算文件摘要，既可校验副本也可生成数据版本指纹。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_sparse_csv(path: Path) -> list[dict[str, float | None]]:
    """读取解码CSV并保留空信号，后续按CAN最近值前向保持。"""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return [
            {key: None if value in (None, "") else float(value) for key, value in row.items()}
            for row in csv.DictReader(stream)
        ]


def forward_filled_samples(rows: list[dict[str, float | None]]) -> list[dict[str, float]]:
    """把稀疏异步CAN信号转换为可执行规则使用的同时间样本。"""
    names = ("speed_kmh", "accel_pedal_pct", "brake_pedal", "steer_deg")
    latest: dict[str, float | None] = {name: None for name in names}
    samples: list[dict[str, float]] = []
    for row in rows:
        for name in names:
            if row.get(name) is not None:
                latest[name] = float(row[name])
        if latest["speed_kmh"] is None:
            continue
        samples.append({"time_s": float(row["time_s"]), **{name: value for name, value in latest.items() if value is not None}})
    return samples


def first_crossing(samples: list[dict[str, float]], target: float, direction: str, start: int = 1) -> int | None:
    """查找车速首次向上或向下穿越目标值的位置。"""
    for index in range(max(1, start), len(samples)):
        left, right = samples[index - 1]["speed_kmh"], samples[index]["speed_kmh"]
        if direction == "up" and left <= target <= right:
            return index
        if direction == "down" and left >= target >= right:
            return index
    return None


def signal_fraction(samples: list[dict[str, float]], name: str, predicate: Any) -> float | None:
    """计算窗口内满足条件的信号比例；信号完全缺失时返回None。"""
    values = [row[name] for row in samples if name in row]
    return sum(bool(predicate(value)) for value in values) / len(values) if values else None


def signal_range(samples: list[dict[str, float]], name: str) -> float | None:
    """计算信号范围，用于检查方向盘是否基本保持不动。"""
    values = [row[name] for row in samples if name in row]
    return max(values) - min(values) if values else None


def acceleration_checks(samples: list[dict[str, float]], rules: dict[str, Any]) -> list[dict[str, Any]]:
    """检查起始车速、目标车速、全油门保持、制动和方向稳定。"""
    if not samples:
        return [{"rule": "speed_data", "passed": False, "reason": "没有有效车速样本"}]
    start_low, start_high = map(float, rules["start_speed_range_kmh"])
    target = float(rules["target_speed_kmh"])
    tolerance = float(rules["target_speed_tolerance_kmh"])
    target_index = first_crossing(samples, target - tolerance, "up")
    throttle_threshold = float(rules["full_throttle_min_pct"])
    engagement_index = next(
        (index for index, sample in enumerate(samples) if sample.get("accel_pedal_pct", -1.0) >= throttle_threshold),
        None,
    )
    # 超越加速数据通常包含踩油门前的稳速准备段，只检查真正执行全油门后的工况窗口。
    window_end = target_index + 1 if target_index is not None else len(samples)
    window = samples[engagement_index:window_end] if engagement_index is not None else []
    throttle_fraction = signal_fraction(window, "accel_pedal_pct", lambda value: value >= throttle_threshold)
    brake_fraction = signal_fraction(window, "brake_pedal", lambda value: value > float(rules["brake_active_threshold"]))
    steer_delta = signal_range(window, "steer_deg")
    return [
        {"rule": "start_speed", "passed": start_low <= samples[0]["speed_kmh"] <= start_high,
         "observed": samples[0]["speed_kmh"], "expected": [start_low, start_high], "reason": "起始车速不符合工况"},
        {"rule": "target_speed", "passed": target_index is not None, "observed": max(row["speed_kmh"] for row in samples),
         "expected": f">={target - tolerance:g}", "reason": "未达到目标车速"},
        {"rule": "full_throttle", "passed": throttle_fraction is not None and throttle_fraction >= float(rules["full_throttle_min_fraction"]),
         "observed": throttle_fraction, "expected": rules["full_throttle_min_fraction"], "reason": "全油门保持比例不足或信号缺失"},
        {"rule": "no_brake", "passed": brake_fraction is not None and brake_fraction <= float(rules["brake_max_fraction"]),
         "observed": brake_fraction, "expected": rules["brake_max_fraction"], "reason": "工况窗口内存在制动或信号缺失"},
        {"rule": "steer_stable", "passed": steer_delta is None or steer_delta <= float(rules["steer_range_max_deg"]),
         "observed": steer_delta, "expected": rules["steer_range_max_deg"], "reason": "方向盘变化超限"},
    ]


def coasting_checks(samples: list[dict[str, float]], rules: dict[str, Any]) -> list[dict[str, Any]]:
    """严格检查50到30窗口、零油门、零制动和方向稳定。"""
    start_speed, end_speed = map(float, rules["window_kmh"])
    start = first_crossing(samples, start_speed, "down")
    end = first_crossing(samples, end_speed, "down", start or 1) if start is not None else None
    window = samples[start:end + 1] if start is not None and end is not None else []
    accelerator_fraction = signal_fraction(window, "accel_pedal_pct", lambda value: value > float(rules["accelerator_max_pct"]))
    brake_fraction = signal_fraction(window, "brake_pedal", lambda value: value > float(rules["brake_active_threshold"]))
    steer_delta = signal_range(window, "steer_deg")
    return [
        {"rule": "strict_speed_window", "passed": bool(window), "observed": [start, end],
         "expected": [start_speed, end_speed], "reason": "缺少严格50到30 km/h向下穿越窗口"},
        {"rule": "zero_accelerator", "passed": accelerator_fraction is not None and accelerator_fraction <= float(rules["accelerator_max_fraction"]),
         "observed": accelerator_fraction, "expected": rules["accelerator_max_fraction"], "reason": "滑行窗口内存在油门或信号缺失"},
        {"rule": "no_brake", "passed": brake_fraction is not None and brake_fraction <= float(rules["brake_max_fraction"]),
         "observed": brake_fraction, "expected": rules["brake_max_fraction"], "reason": "滑行窗口内存在制动或信号缺失"},
        {"rule": "steer_stable", "passed": steer_delta is None or steer_delta <= float(rules["steer_range_max_deg"]),
         "observed": steer_delta, "expected": rules["steer_range_max_deg"], "reason": "方向盘变化超限"},
    ]


def repeat_index(path: Path, fallback: int) -> int:
    """从文件名末尾提取试验编号，无法识别时使用稳定排序编号。"""
    matches = re.findall(r"(?:_|-)(\d+)(?=(?:__|\.|_condition))", path.name)
    return int(matches[-1]) if matches else fallback


def find_original(decoded: Path, role: str, index: int, data_root: Path, registry: dict[str, Any]) -> Path | None:
    """按工况目录和稳定编号关联原始BLF，避免仅依赖含中文的完整文件名。"""
    source_dir = data_root / Path(registry["conditions"][role]["source_subdirectory"])
    blf_files = sorted(source_dir.glob("*.blf"))
    return blf_files[index - 1] if 0 < index <= len(blf_files) else None


def review_for(role: str, index: int, reviews: dict[str, Any]) -> dict[str, Any] | None:
    """读取独立人工复核记录，自动判定原文始终保持不变。"""
    return reviews.get("records", {}).get(f"{role}:{index}")


def automatic_status(checks: list[dict[str, Any]], required_evidence: list[str], review: dict[str, Any] | None,
                     configured_exclusion: str | None) -> tuple[str, list[str]]:
    """先处理明确不合格，再处理人工复核；缺关键证据时保持待复核。"""
    failures = [str(item["reason"]) for item in checks if not item["passed"]]
    if configured_exclusion:
        return "rejected", [configured_exclusion]
    if failures:
        return "rejected", failures
    if review and review.get("decision") == "rejected":
        return "rejected", [f"人工复核：{review.get('basis', '未填写依据')}"]
    if review and review.get("decision") == "accepted":
        # 新版记录可限定确认范围，防止“只确认路面”意外覆盖挡位等机器证据缺失。
        reviewed_scope = set(review.get("evidence_scope", required_evidence))
        unresolved = [name for name in required_evidence if name not in reviewed_scope]
        if unresolved:
            return "pending_review", [f"缺少关键证据：{', '.join(unresolved)}"]
        return "accepted", [f"人工复核：{review.get('basis', '未填写依据')}"]
    if required_evidence:
        return "pending_review", [f"缺少关键证据：{', '.join(required_evidence)}"]
    return "accepted", ["全部自动规则通过"]


def condition_time_window(samples: list[dict[str, float]], role: str, rules: dict[str, Any]) -> tuple[float, float] | None:
    """返回准入检查使用的工况时间窗，供原始CAN证据使用同一口径。"""
    if role == "coasting":
        start_speed, end_speed = map(float, rules["window_kmh"])
        start = first_crossing(samples, start_speed, "down")
        end = first_crossing(samples, end_speed, "down", start or 1) if start is not None else None
    else:
        threshold = float(rules["full_throttle_min_pct"])
        start = next((index for index, row in enumerate(samples) if row.get("accel_pedal_pct", -1.0) >= threshold), None)
        end = first_crossing(samples, float(rules["target_speed_kmh"]) - float(rules["target_speed_tolerance_kmh"]), "up")
    if start is None or end is None or end <= start:
        return None
    return float(samples[start]["time_s"]), float(samples[end]["time_s"])


def extract_can_evidence(original: Path | None, samples: list[dict[str, float]], role: str,
                         rules: dict[str, Any], database: Any | None) -> dict[str, Any]:
    """从原始BLF提取档位、驾驶模式和回收证据，避免把统一CSV缺列误判为无证据。"""
    window = condition_time_window(samples, role, rules)
    if original is None or database is None or window is None:
        return {}
    decoded = decode_selected_signals(original, database)
    gear = category_summary(window_values(decoded, "actual_gear", window), GEAR_NAMES)
    drive_mode = category_summary(window_values(decoded, "drive_mode", window), DRIVE_MODE_NAMES)
    expected_gear = "N" if role == "coasting" else "D"
    gear_fraction = float(gear["fractions"].get(expected_gear, 0.0))
    comfort_fraction = float(drive_mode["fractions"].get("COMFORT", 0.0))
    evidence: dict[str, Any] = {
        "gear": {
            "verified": gear_fraction >= 0.95,
            "source": "原始BLF+DBC",
            "expected": expected_gear,
            "observed_fraction": gear_fraction,
        },
        "drive_mode": {
            "verified": comfort_fraction >= 0.98,
            "source": "原始BLF+DBC",
            "expected": "COMFORT",
            "observed_fraction": comfort_fraction,
        },
    }
    if role == "coasting":
        regen = numeric_summary(window_values(decoded, "regen_torque_d_nm", window), invalid_above=4094.0)
        maximum = regen.get("maximum")
        evidence["regeneration"] = {
            "verified": maximum is not None and abs(float(maximum)) <= 0.5,
            "source": "原始BLF+DBC",
            "expected": "回收扭矩绝对值不超过0.5 Nm",
            "observed_max_nm": maximum,
        }
    return evidence


def missing_required_evidence(required: list[str], evidence: dict[str, Any]) -> list[str]:
    """只保留尚未被机器证据验证的必需项，路面等不可观测条件继续人工复核。"""
    return [name for name in required if not bool(evidence.get(name, {}).get("verified"))]


def load_admission_database(data_root: Path) -> Any | None:
    """加载原始CAN证据所需DBC；缺失时继续生成待复核清单而不是中断分类。"""
    dbc_path = next(data_root.glob("*.dbc"), None)
    if dbc_path is None:
        return None
    _, cantools = load_dependencies(PROJECT_ROOT.parents[1])
    return cantools.database.load_file(str(dbc_path))


def copy_record_files(record_dir: Path, decoded: Path, original: Path | None) -> dict[str, str | None]:
    """复制原始与解码文件并校验摘要，绝不移动或修改源文件。"""
    record_dir.mkdir(parents=True, exist_ok=True)
    decoded_copy = record_dir / "解码数据.csv"
    shutil.copy2(decoded, decoded_copy)
    original_copy = None
    if original is not None:
        original_copy = record_dir / original.name
        shutil.copy2(original, original_copy)
    return {
        "decoded_copy": str(decoded_copy),
        "decoded_sha256": sha256_file(decoded_copy),
        "original_copy": str(original_copy) if original_copy else None,
        "original_sha256": sha256_file(original_copy) if original_copy else None,
    }


def write_record_readme(record_dir: Path, record: dict[str, Any]) -> None:
    """为每条分类数据生成人工可读原因说明。"""
    reasons = "\n".join(f"- {reason}" for reason in record["reasons"])
    (record_dir / "README.md").write_text(
        f"# {record['condition_label']} 第{record['repeat_index']}次\n\n"
        f"准入状态：**{STATUS_FOLDERS[record['status']]}**\n\n## 判定原因\n\n{reasons}\n\n"
        "本目录是分类副本，原始实车数据未被移动或修改。`admission_record.json`保存完整自动规则、人工复核引用和SHA256。\n",
        encoding="utf-8",
    )


def assign_splits(records: list[dict[str, Any]], project_config: dict[str, Any]) -> None:
    """优先沿用既有试验编号划分，新增样本按稳定顺序补入标定或验证集。"""
    minimum = project_config["data_admission"]["minimum_samples"]
    configured = project_config["agent"]["dataset_splits"]
    for role in {record["role"] for record in records}:
        accepted = [record for record in records if record["role"] == role and record["status"] == "accepted"]
        split = configured.get(role, {})
        for record in accepted:
            index = record["repeat_index"]
            if index in split.get("calibration", []):
                record["dataset_split"] = "calibration"
            elif index in split.get("validation", []):
                record["dataset_split"] = "validation"
        unassigned = [record for record in accepted if not record.get("dataset_split")]
        for record in unassigned:
            calibration_count = sum(item.get("dataset_split") == "calibration" for item in accepted)
            validation_count = sum(item.get("dataset_split") == "validation" for item in accepted)
            record["dataset_split"] = "validation" if validation_count < int(minimum["validation"]) else "calibration"
        for record in records:
            if record["role"] == role and record["status"] != "accepted":
                record["dataset_split"] = "excluded"


def build_admission_batch(batch_id: str | None = None, copy_files: bool = True) -> dict[str, Any]:
    """检查全部已启用工况并生成一次不可覆盖的数据准入批次。"""
    paths = load_runtime_paths()
    project_config = load_project_config()
    registry = read_json(REGISTRY_PATH)
    reviews = read_json(REVIEWS_PATH, {"records": {}})
    database = load_admission_database(paths["data_root"])
    decoded_root = paths["output_root"] / "解码CSV_单位修正"
    batch = batch_id or datetime.now().strftime("batch_%Y%m%d_%H%M%S")
    output = paths["output_root"] / "数据准入" / batch
    if output.exists():
        raise FileExistsError(f"数据准入批次已存在，拒绝覆盖：{output}")
    records: list[dict[str, Any]] = []
    exclusions = project_config["agent"]["dataset_splits"]
    for role, condition in registry["conditions"].items():
        if not condition.get("enabled"):
            continue
        decoded_files = sorted((decoded_root / Path(condition["source_subdirectory"])).glob("*.csv"))
        for fallback, decoded in enumerate(decoded_files, start=1):
            index = repeat_index(decoded, fallback)
            samples = forward_filled_samples(read_sparse_csv(decoded))
            checks = coasting_checks(samples, condition["admission"]) if role == "coasting" else acceleration_checks(samples, condition["admission"])
            exclusion = exclusions.get(role, {}).get("excluded", {}).get(str(index))
            review = review_for(role, index, reviews)
            original = find_original(decoded, role, index, paths["data_root"], registry)
            evidence = extract_can_evidence(original, samples, role, condition["admission"], database)
            missing_evidence = missing_required_evidence(list(condition.get("required_manual_evidence", [])), evidence)
            status, reasons = automatic_status(checks, missing_evidence, review, exclusion)
            record: dict[str, Any] = {
                "record_id": f"{role}:{index}", "role": role, "condition_label": condition["label_zh"],
                "domain": condition["domain"], "repeat_index": index, "status": status, "reasons": reasons,
                "automatic_checks": checks, "manual_review": review, "decoded_source": str(decoded),
                "condition_evidence": evidence, "missing_required_evidence": missing_evidence,
                "decoded_source_sha256": sha256_file(decoded), "original_source": str(original) if original else None,
                "original_source_sha256": sha256_file(original) if original else None,
            }
            record_dir = output / STATUS_FOLDERS[status] / role / f"repeat_{index:02d}"
            if copy_files:
                record.update(copy_record_files(record_dir, decoded, original))
            write_json(record_dir / "admission_record.json", record)
            write_record_readme(record_dir, record)
            records.append(record)
    assign_splits(records, project_config)
    data_fingerprint = hashlib.sha256("".join(sorted(record["decoded_source_sha256"] for record in records)).encode("ascii")).hexdigest()[:16]
    manifest = {
        "version": "1.0", "batch_id": batch, "created_at": datetime.now().isoformat(timespec="seconds"),
        "condition_registry_version": registry["version"], "data_fingerprint": data_fingerprint,
        "source_data_root": str(paths["data_root"]), "source_data_modified": False,
        "counts": {status: sum(record["status"] == status for record in records) for status in STATUS_FOLDERS},
        "records": records,
    }
    write_json(output / "admission_manifest.json", manifest)
    for status, label in STATUS_FOLDERS.items():
        folder = output / label
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "README.md").write_text(
            f"# {label}数据\n\n本目录包含本批次判定为“{label}”的数据分类副本。原始实车数据目录保持不变。\n",
            encoding="utf-8",
        )
    (output / "README.md").write_text(
        "# 实车数据准入批次\n\n"
        "`admission_manifest.json`是优化器唯一可读取的准入清单。合格数据才可进入标定/验证；"
        "不合格和待人工复核数据默认排除。分类目录均为副本，原始BLF未移动。\n\n"
        "人工复核写入`config/admission_reviews.local.json`后，应生成新的批次，旧批次保持不变以便审计。\n",
        encoding="utf-8",
    )
    return manifest


def latest_admission_manifest() -> Path | None:
    """返回最新完整准入批次清单。"""
    root = load_runtime_paths()["output_root"] / "数据准入"
    manifests = list(root.glob("*/admission_manifest.json")) if root.exists() else []
    return max(manifests, key=lambda path: path.stat().st_mtime) if manifests else None


def record_manual_review(role: str, index: int, decision: str, reviewer: str, basis: str) -> dict[str, Any]:
    """追加独立人工复核记录，不回写或覆盖任何自动判定结果。"""
    if decision not in {"accepted", "rejected"}:
        raise ValueError("人工复核decision必须为accepted或rejected")
    reviews = read_json(REVIEWS_PATH, {"version": "1.0", "records": {}})
    reviews.setdefault("records", {})[f"{role}:{index}"] = {
        "decision": decision, "reviewer": reviewer.strip(), "basis": basis.strip(),
        "reviewed_at": datetime.now().isoformat(timespec="seconds"),
    }
    if not reviewer.strip() or not basis.strip():
        raise ValueError("人工复核必须填写复核人和依据")
    write_json(REVIEWS_PATH, reviews)
    return reviews["records"][f"{role}:{index}"]


def main() -> None:
    """命令行生成准入批次或写入人工复核。"""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="生成新的自动准入批次")
    run_parser.add_argument("--batch-id")
    review_parser = subparsers.add_parser("review", help="记录人工复核结论")
    review_parser.add_argument("--role", required=True)
    review_parser.add_argument("--repeat", type=int, required=True)
    review_parser.add_argument("--decision", choices=("accepted", "rejected"), required=True)
    review_parser.add_argument("--reviewer", required=True)
    review_parser.add_argument("--basis", required=True)
    args = parser.parse_args()
    if args.command == "run":
        result = build_admission_batch(args.batch_id)
        print(json.dumps({"batch_id": result["batch_id"], "counts": result["counts"]}, ensure_ascii=False))
    else:
        result = record_manual_review(args.role, args.repeat, args.decision, args.reviewer, args.basis)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
