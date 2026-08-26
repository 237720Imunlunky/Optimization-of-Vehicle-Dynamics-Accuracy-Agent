"""计算实车与 Carsim CSV 的纵向动力学一致性指标。"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any


DEFAULT_COASTING_WINDOW_KMH = (50.0, 30.0)


def normalize_coasting_window(window_kmh: tuple[float, float] | list[float] | None) -> tuple[float, float]:
    """校验并标准化滑行评价窗口，运行时由config.json传入。"""
    if window_kmh is None:
        return DEFAULT_COASTING_WINDOW_KMH
    if len(window_kmh) != 2:
        raise ValueError("滑行窗口必须包含起始和结束两个车速")
    start_speed, end_speed = float(window_kmh[0]), float(window_kmh[1])
    if start_speed <= end_speed:
        raise ValueError("滑行窗口必须是递减车速区间")
    return start_speed, end_speed


def read_rows(path: Path) -> list[dict[str, float]]:
    """读取 CSV，并兼容实车字段名与 CarSim 标准字段名。"""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = []
        for raw in csv.DictReader(stream):
            row = {key: (None if value in (None, "") else float(value)) for key, value in raw.items()}
            if row.get("speed_kmh") is None and row.get("vxdot") is not None:
                row["speed_kmh"] = float(row["vxdot"]) * 3.6
            if row.get("accel_mps2") is None and row.get("ax") is not None:
                row["accel_mps2"] = float(row["ax"])
            rows.append(row)
    return rows


def resample_rows(rows: list[dict], step_s: float = 0.01) -> list[dict]:
    """将稀疏 CAN 报文和 CarSim 等间隔输出统一到 10 ms 时间栅格。"""
    valid_times = [float(row["time_s"]) for row in rows if row.get("time_s") is not None]
    if not valid_times:
        return []
    end = max(valid_times)
    columns = ("speed_kmh", "accel_mps2")
    output = []
    source_index = 0
    latest = {column: None for column in columns}
    grid_count = int(end / step_s) + 1
    for index in range(grid_count):
        target_time = index * step_s
        while source_index < len(rows) and (rows[source_index].get("time_s") or 0.0) <= target_time:
            for column in columns:
                if rows[source_index].get(column) is not None:
                    latest[column] = rows[source_index][column]
            source_index += 1
        output.append({"time_s": target_time, **latest})
    return output


def values(rows: list[dict], column: str) -> list[float]:
    """获取某列有效数值。"""
    return [float(row[column]) for row in rows if row.get(column) is not None]


def interpolate_row(left: dict, right: dict, target_speed_kmh: float) -> dict:
    """在两个时间栅格点之间插值得到指定车速边界。"""
    v0, v1 = float(left["speed_kmh"]), float(right["speed_kmh"])
    ratio = 0.0 if abs(v1 - v0) <= 1e-12 else (target_speed_kmh - v0) / (v1 - v0)
    row = {"time_s": float(left["time_s"]) + ratio * (float(right["time_s"]) - float(left["time_s"])), "speed_kmh": target_speed_kmh}
    for column in ("accel_mps2",):
        if left.get(column) is not None and right.get(column) is not None:
            row[column] = float(left[column]) + ratio * (float(right[column]) - float(left[column]))
    return row


def find_speed_crossing(rows: list[dict], target_kmh: float, direction: str, start_index: int = 1) -> tuple[int, dict] | None:
    """查找指定方向的车速穿越点，并返回右侧索引和插值边界。"""
    for index in range(max(1, start_index), len(rows)):
        left, right = rows[index - 1], rows[index]
        if left.get("speed_kmh") is None or right.get("speed_kmh") is None:
            continue
        v0, v1 = float(left["speed_kmh"]), float(right["speed_kmh"])
        crossed = v0 <= target_kmh <= v1 if direction == "up" else v0 >= target_kmh >= v1
        crossed = crossed and abs(v1 - v0) > 1e-12
        if crossed:
            return index, interpolate_row(left, right, target_kmh)
    return None


def align_maneuver(
    rows: list[dict], role: str, coasting_window_kmh: tuple[float, float] | list[float] | None = None,
) -> list[dict]:
    """按工况车速边界裁切并把起点归零，保证实车和仿真比较同一阶段。"""
    coasting_start, coasting_end = normalize_coasting_window(coasting_window_kmh)
    boundaries = {
        "zero_to_100": (0.0, 100.0, "up"),
        "overtaking": (60.0, 100.0, "up"),
        "coasting": (coasting_start, coasting_end, "down"),
    }
    if role not in boundaries or not rows:
        return rows
    start_speed, end_speed, direction = boundaries[role]

    start = find_speed_crossing(rows, start_speed, direction)
    if start is None:
        # 部分60-100实车文件在记录开始时已经略高于60 km/h。此时从首个有效点
        # 评价“实际记录起点到100”，并在报告中保留实际起始车速，避免错位拼接曲线。
        first_valid = next((index for index, row in enumerate(rows) if row.get("speed_kmh") is not None), None)
        if first_valid is None:
            return rows
        first_speed = float(rows[first_valid]["speed_kmh"])
        is_inside_window = start_speed <= first_speed < end_speed if direction == "up" else start_speed >= first_speed > end_speed
        if not is_inside_window:
            return rows
        start_index, start_row = first_valid + 1, {**rows[first_valid]}
    else:
        start_index, start_row = start
    end = find_speed_crossing(rows, end_speed, direction, start_index)
    if end is None:
        return rows
    end_index, end_row = end
    segment = [start_row, *rows[start_index:end_index], end_row]
    start_time = float(segment[0]["time_s"])
    return [{**row, "time_s": float(row["time_s"]) - start_time} for row in segment]


def r2_score(truth: list[float], sim: list[float]) -> float:
    """计算 R²，使用实车均值作为基准。"""
    if len(truth) < 2:
        return 0.0
    mean = sum(truth) / len(truth)
    denominator = sum((item - mean) ** 2 for item in truth)
    return 0.0 if denominator <= 1e-12 else 1.0 - sum((a - b) ** 2 for a, b in zip(truth, sim)) / denominator


def nrmse(truth: list[float], sim: list[float]) -> float:
    """计算 RMSE/实车量程。"""
    if not truth:
        return float("inf")
    span = max(truth) - min(truth)
    rmse = math.sqrt(sum((a - b) ** 2 for a, b in zip(truth, sim)) / len(truth))
    return rmse / span if span > 1e-12 else float("inf")


def feature_score(sim_value: float, truth_value: float, floor: float = 0.05) -> float:
    """计算特征相对精度，真值接近零时用工程下限避免除零。"""
    denominator = max(abs(truth_value), floor)
    return max(0.0, 1.0 - abs(sim_value - truth_value) / denominator) * 100.0


def target_time(rows: list[dict], target_kmh: float, direction: str = "up") -> float | None:
    """线性插值计算按指定方向达到目标车速的时间。"""
    points = [(row.get("time_s"), row.get("speed_kmh")) for row in rows]
    points = [(float(t), float(v)) for t, v in points if t is not None and v is not None]
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        crossed = v0 < target_kmh <= v1 if direction == "up" else v0 > target_kmh >= v1
        if crossed:
            ratio = (target_kmh - v0) / (v1 - v0) if v1 != v0 else 0.0
            return t0 + ratio * (t1 - t0)
    return None


def integrated_distance_m(rows: list[dict]) -> float:
    """用梯形积分计算当前评价窗口内的行驶距离，车速单位由km/h转为m/s。"""
    points = [(row.get("time_s"), row.get("speed_kmh")) for row in rows]
    valid = [(float(time_s), float(speed) / 3.6) for time_s, speed in points if time_s is not None and speed is not None]
    return sum((v0 + v1) * 0.5 * (t1 - t0) for (t0, v0), (t1, v1) in zip(valid, valid[1:]) if t1 > t0)


def aggregate_maneuver_score(metrics: dict[str, Any], feature_names: list[str]) -> dict[str, float]:
    """按正式规则聚合：时域拟合占40%，特征精度占60%，同类特征暂按等权。"""
    time_domain = (metrics["speed_r2"]["score_pct"] + metrics["speed_nrmse"]["score_pct"]) / 2.0
    feature_values = [metrics[name]["score_pct"] for name in feature_names if name in metrics]
    feature = sum(feature_values) / len(feature_values) if feature_values else 0.0
    return {
        "time_domain_score_pct": time_domain,
        "feature_score_pct": feature,
        "maneuver_score_pct": 0.4 * time_domain + 0.6 * feature,
    }


def compare_pair(
    truth_path: Path,
    sim_path: Path,
    role: str,
    rules: dict,
    coasting_window_kmh: tuple[float, float] | list[float] | None = None,
) -> dict[str, Any]:
    """比较一个工况，输出原始值、得分和是否通过。"""
    window = normalize_coasting_window(coasting_window_kmh)
    truth = align_maneuver(resample_rows(read_rows(truth_path)), role, window)
    sim = align_maneuver(resample_rows(read_rows(sim_path)), role, window)
    truth_speed, sim_speed = values(truth, "speed_kmh"), values(sim, "speed_kmh")
    count = min(len(truth_speed), len(sim_speed))
    truth_speed, sim_speed = truth_speed[:count], sim_speed[:count]
    speed_r2 = r2_score(truth_speed, sim_speed)
    speed_nrmse = nrmse(truth_speed, sim_speed)
    metrics: dict[str, Any] = {
        "speed_r2": {"value": speed_r2, "score_pct": max(0.0, speed_r2) * 100, "passed": speed_r2 >= rules["speed_r2_min"]},
        "speed_nrmse": {"value": speed_nrmse, "score_pct": max(0.0, 1.0 - speed_nrmse) * 100, "passed": speed_nrmse <= rules["speed_nrmse_max"]},
    }
    feature_names: list[str] = []
    if role in {"zero_to_100", "overtaking"}:
        truth_ax, sim_ax = values(truth, "accel_mps2"), values(sim, "accel_mps2")
        peak_truth = max((abs(v) for v in truth_ax), default=0.0)
        peak_sim = max((abs(v) for v in sim_ax), default=0.0)
        peak_score = feature_score(peak_sim, peak_truth)
        metrics["peak_ax"] = {"truth": peak_truth, "sim": peak_sim, "score_pct": peak_score,
                              "passed": peak_score >= rules["peak_ax_accuracy_min_pct"]}
        feature_names.append("peak_ax")
    else:
        truth_distance = integrated_distance_m(truth)
        sim_distance = integrated_distance_m(sim)
        distance_score = feature_score(sim_distance, truth_distance)
        metrics["coasting_distance"] = {"truth_m": truth_distance, "sim_m": sim_distance,
                                         "score_pct": distance_score,
                                         "passed": distance_score >= rules["coasting_distance_accuracy_min_pct"]}
        feature_names.append("coasting_distance")
    target = 100.0 if role in {"zero_to_100", "overtaking"} else window[1]
    direction = "down" if role == "coasting" else "up"
    truth_time, sim_time = target_time(truth, target, direction), target_time(sim, target, direction)
    if truth_time is not None and sim_time is not None:
        score = feature_score(sim_time, truth_time)
        metrics["target_time"] = {"target_kmh": target, "truth_s": truth_time, "sim_s": sim_time, "score_pct": score, "passed": score >= rules["target_time_accuracy_min_pct"]}
        feature_names.append("target_time")
    scores = aggregate_maneuver_score(metrics, feature_names)
    return {"truth": str(truth_path), "simulation": str(sim_path), "role": role,
            "window_kmh": list(window) if role == "coasting" else None, "metrics": metrics,
            "actual_start_speed_kmh": truth[0].get("speed_kmh") if truth else None,
            **scores, "feature_weights": "equal_within_maneuver",
            "all_metrics_passed": all(item["passed"] for item in metrics.values())}


def aggregate(results: list[dict[str, Any]], config: dict) -> dict[str, Any]:
    """按加速/滑行权重聚合纵向总分，并给出 80% 和 85% 两种判定。"""
    groups = {"acceleration": [r["maneuver_score_pct"] for r in results if r["role"] in {"zero_to_100", "overtaking"}],
              "coasting": [r["maneuver_score_pct"] for r in results if r["role"] == "coasting"]}
    group_scores = {key: (sum(value) / len(value) if value else None) for key, value in groups.items()}
    weights = config["longitudinal_weights"]
    available = [(group_scores[key], weights[key]) for key in group_scores if group_scores[key] is not None]
    total = sum(score * weight for score, weight in available) / sum(weight for _, weight in available) if available else 0.0
    formal_threshold = float(config["formal_acceptance_threshold_pct"])
    complete = all(group_scores[key] is not None for key in ("acceleration", "coasting"))
    return {"group_scores_pct": group_scores, "weights": weights, "longitudinal_score_pct": total,
            "data_complete": complete,
            "formal_acceptance_threshold_pct": formal_threshold,
            "formal_passed": complete and total >= formal_threshold,
            "pass_80_pct": total >= 80.0,
            "pass_85_pct": total >= 85.0}
