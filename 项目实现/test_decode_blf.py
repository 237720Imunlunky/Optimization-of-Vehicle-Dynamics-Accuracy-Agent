"""验证BLF解码的文件级信号选择和单位换算。"""

from decode_blf import STANDARD_GRAVITY_MPS2, choose_file_signal, normalize_signal_value


def test_choose_file_signal_uses_first_available_candidate() -> None:
    """高优先级信号无样本时，应选择下一个真实存在的信号。"""
    samples = {"ACU_LgtA": [], "LgtA": [(0.1, 0.5)], "EPBM_CDPAx": [(0.1, 0.0)]}
    selected = choose_file_signal(samples, ["ACU_LgtA", "LgtA", "EPBM_CDPAx"])
    assert selected == "LgtA"


def test_acceleration_g_is_converted_to_mps2() -> None:
    """DBC单位为g的纵向加速度必须转换为m/s2。"""
    converted = normalize_signal_value("accel_mps2", "g", 0.5)
    assert converted == 0.5 * STANDARD_GRAVITY_MPS2


def test_acceleration_mps2_keeps_original_value() -> None:
    """已经是m/s2的信号不得再次缩放。"""
    assert normalize_signal_value("accel_mps2", "m/s2", 3.2) == 3.2
