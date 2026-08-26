"""运行一条使用实车油门 Trace 的 Carsim 加速仿真。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    """复制 Trace 参数到 ASCII 运行目录并调用求解器。"""
    root = Path(__file__).resolve().parent
    source = root / "输出" / "Trace控制输入" / "condition_01_trace_control"
    runtime = Path("F:/Carsim/AgentRuntime/parameter_agent/condition_01_trace_control/repeat_01")
    archive = source / "carsim_run"
    runtime.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "Run_all.par", runtime / "Run_all.par")
    animator = Path("F:/Carsim/AgentRuntime/parameter_agent/iteration_222/carsim2023_conditions_20260823_231653/condition_01_0_to_100_wot/repeat_01/animator.par")
    if animator.exists():
        shutil.copy2(animator, runtime / "animator.par")
    carsim_root = Path("F:/Carsim/Carsim2023/Carsim2023.2/install")
    prefix = runtime / "result"
    sim = runtime / "run.sim"
    sim.write_text("\n".join([
        "SIMFILE", f"FILEBASE {prefix}", f"INPUT {runtime / 'Run_all.par'}",
        f"INPUTARCHIVE {prefix}_all.par", f"ECHO {prefix}_echo.par", f"FINAL {prefix}_end.par",
        f"LOGFILE {prefix}_log.txt", f"ERDFILE {prefix}.vs", f"PROGDIR {carsim_root}", f"DATADIR {runtime}",
        f"RESOURCEDIR {carsim_root / 'Resources'}", "PRODUCT_ID CarSim", "PRODUCT_VER 2023.2",
        "VEHICLE_CODE i_i", "EXT_MODEL_STEP 0.00050000", f"DLLFILE {carsim_root / 'Programs' / 'solvers' / 'carsim_64.dll'}", "END", "",
    ]), encoding="ascii")
    wrapper = carsim_root / "Programs" / "VS_SolverWrapper_CLI_64.exe"
    result = subprocess.run([str(wrapper), "-sim", str(sim)], cwd=runtime, capture_output=True, text=True, encoding="cp1252", errors="replace", check=False)
    (runtime / "solver_stdout.txt").write_text(result.stdout, encoding="utf-8")
    (runtime / "solver_stderr.txt").write_text(result.stderr, encoding="utf-8")
    archive.mkdir(parents=True, exist_ok=True)
    for name in ("Run_all.par", "run.sim", "result.vs", "result.vsb", "result_end.par", "result_log.txt", "solver_stdout.txt", "solver_stderr.txt"):
        path = runtime / name
        if path.exists():
            shutil.copy2(path, archive / name)
    status = {"return_code": result.returncode, "status": "completed" if result.returncode == 0 else "failed", "runtime": str(runtime), "archive": str(archive)}
    (archive / "run_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()

