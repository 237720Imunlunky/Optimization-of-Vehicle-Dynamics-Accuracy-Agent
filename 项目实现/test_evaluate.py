"""对评价核心函数做轻量自检，便于后续修改后快速回归。"""

from evaluate_longitudinal import (
    aggregate_maneuver_score,
    align_maneuver,
    feature_score,
    integrated_distance_m,
    nrmse,
    r2_score,
    target_time,
)


def test_perfect_curve() -> None:
    """完全一致的曲线应得到满分。"""
    values = [0.0, 1.0, 2.0, 3.0]
    assert r2_score(values, values) == 1.0
    assert nrmse(values, values) == 0.0
    assert feature_score(10.0, 10.0) == 100.0


def test_degraded_curve() -> None:
    """偏差曲线不应被误判为满分。"""
    truth = [0.0, 1.0, 2.0, 3.0]
    sim = [0.0, 0.5, 2.5, 4.0]
    assert r2_score(truth, sim) < 1.0
    assert nrmse(truth, sim) > 0.0
    assert feature_score(8.0, 10.0) < 100.0


def test_coasting_alignment_uses_50_to_30_window() -> None:
    """滑行评价必须裁切50到30 km/h并把时间起点归零。"""
    rows = [
        {"time_s": 0.0, "speed_kmh": 55.0, "accel_mps2": -0.2},
        {"time_s": 1.0, "speed_kmh": 50.0, "accel_mps2": -0.2},
        {"time_s": 3.0, "speed_kmh": 40.0, "accel_mps2": -0.2},
        {"time_s": 5.0, "speed_kmh": 30.0, "accel_mps2": -0.2},
        {"time_s": 6.0, "speed_kmh": 25.0, "accel_mps2": -0.2},
    ]
    aligned = align_maneuver(rows, "coasting")
    assert aligned[0]["speed_kmh"] == 50.0
    assert aligned[0]["time_s"] == 0.0
    assert aligned[-1]["speed_kmh"] == 30.0
    assert target_time(aligned, 30.0, "down") == 4.0


def test_overtaking_alignment_uses_60_to_100_window() -> None:
    """超越加速评价必须从60 km/h开始计时。"""
    rows = [
        {"time_s": 0.0, "speed_kmh": 55.0},
        {"time_s": 1.0, "speed_kmh": 60.0},
        {"time_s": 2.0, "speed_kmh": 80.0},
        {"time_s": 3.0, "speed_kmh": 100.0},
    ]
    aligned = align_maneuver(rows, "overtaking")
    assert aligned[0]["speed_kmh"] == 60.0
    assert target_time(aligned, 100.0) == 2.0


def test_integrated_distance_uses_speed_time_area() -> None:
    """匀速36 km/h行驶10秒的积分距离应为100米。"""
    rows = [{"time_s": 0.0, "speed_kmh": 36.0}, {"time_s": 10.0, "speed_kmh": 36.0}]
    assert integrated_distance_m(rows) == 100.0


def test_formal_maneuver_aggregation_formula() -> None:
    """单工况必须按40%时域和60%特征聚合，不能直接平均全部指标。"""
    metrics = {
        "speed_r2": {"score_pct": 90.0},
        "speed_nrmse": {"score_pct": 80.0},
        "peak_ax": {"score_pct": 70.0},
        "target_time": {"score_pct": 50.0},
    }
    result = aggregate_maneuver_score(metrics, ["peak_ax", "target_time"])
    assert result["time_domain_score_pct"] == 85.0
    assert result["feature_score_pct"] == 60.0
    assert result["maneuver_score_pct"] == 70.0


def test_overtaking_accepts_actual_recording_start_above_60() -> None:
    """记录从65 km/h开始时应保留真实起点，不能把曲线与60 km/h仿真错位。"""
    rows = [
        {"time_s": 0.0, "speed_kmh": 65.0},
        {"time_s": 1.0, "speed_kmh": 80.0},
        {"time_s": 2.0, "speed_kmh": 100.0},
    ]
    aligned = align_maneuver(rows, "overtaking")
    assert aligned[0]["speed_kmh"] == 65.0
    assert target_time(aligned, 100.0) == 2.0
