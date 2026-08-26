"""验证滑行工况生成中的关键单位和控制输入。"""

from pathlib import Path

from run_coast_simulation import build_par


def test_build_par_sets_50_kmh_and_zero_throttle(tmp_path: Path) -> None:
    """初速和油门应统一，且N挡滑行必须显式关闭松油门回收。"""
    source = tmp_path / "source.par"
    output = tmp_path / "output.par"
    source.write_bytes(
        b"TSTOP 10\r\nSV_VXS 13.8889\r\nOPT_REGEN_OFF_THRT 1\r\n"
        b"THROTTLE_ENGINE_TABLE LINEAR_FLAT\r\n0, 1\r\n1, 1\r\nENDTABLE\r\n"
    )
    build_par(source, output, 90.0)
    text = output.read_bytes().decode("ascii")
    assert "SV_VXS 50" in text
    assert "0, 0" in text and "1, 0" in text
    assert "OPT_REGEN_OFF_THRT 0" in text
    assert "TSTOP 90.000" in text
