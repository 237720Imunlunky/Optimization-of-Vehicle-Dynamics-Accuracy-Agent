"""从真实滑行 BLF 解码结果提取 50->30 km/h 仿真边界。"""

from __future__ import annotations

import csv
import json
from pathlib import Path


def read_speed_points(path: Path) -> list[tuple[float, float]]:
    """读取解码 CSV 中有效的时间和车速点。"""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        points = []
        for row in csv.DictReader(stream):
            if row.get("time_s") and row.get("speed_kmh"):
                points.append((float(row["time_s"]), float(row["speed_kmh"])))
        return points


def crossing_time(points: list[tuple[float, float]], target: float, start_index: int = 0) -> tuple[float, int] | None:
    """用线性插值计算首次穿越目标速度的时间。"""
    for index in range(max(1, start_index), len(points)):
        t0, v0 = points[index - 1]
        t1, v1 = points[index]
        if v0 >= target > v1:
            ratio = (v0 - target) / (v0 - v1) if v0 != v1 else 0.0
            return t0 + ratio * (t1 - t0), index
    return None


def build_manifest(decoded_root: Path, output_path: Path) -> dict:
    """为每个滑行文件生成可追溯的 Carsim 工况边界清单。"""
    records = []
    for csv_path in sorted(decoded_root.rglob("*.csv")):
        if "滑行试验" not in str(csv_path) or csv_path.name == "manifest.csv":
            continue
        points = read_speed_points(csv_path)
        start = crossing_time(points, 50.0)
        end = crossing_time(points, 30.0, start[1] if start else 0)
        record = {
            "source_csv": str(csv_path),
            "target": "50_to_30_kmh_coasting",
            "initial_speed_kmh": 50.0,
            "final_speed_kmh": 30.0,
            "start_time_s": start[0] if start else None,
            "end_time_s": end[0] if end else None,
            "duration_s": (end[0] - start[0]) if start and end else None,
            "valid": bool(start and end),
            "note": "仅提取边界，不把实车响应伪装成 Carsim 输出",
        }
        records.append(record)
    manifest = {
        "status": "ready_for_carsim_condition_generation" if records and all(item["valid"] for item in records) else "needs_data_review",
        "condition": "coast_50_to_30",
        "records": records,
        "next_input_required": "Carsim 使用与实车一致的初始车速、油门释放/制动为零和仿真时长后导出 CSV",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    """命令行入口。"""
    root = Path(__file__).resolve().parent
    manifest = build_manifest(root / "输出" / "解码CSV_单位修正", root / "输出" / "滑行工况" / "当前配置基线" / "coast_condition_manifest.json")
    print(json.dumps({"status": manifest["status"], "records": len(manifest["records"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
