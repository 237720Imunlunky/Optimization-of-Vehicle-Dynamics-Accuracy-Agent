"""CarSim纵向动力学参数优化Agent本地交互服务。"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

UI_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = UI_ROOT.parent
STATIC_ROOT = UI_ROOT / "static"
CONFIG_PATH = Path(os.environ.get("VEHICLE_AGENT_LLM_CONFIG", UI_ROOT / "config" / "llm_api.local.json"))
REGISTRY_PATH = PROJECT_ROOT / "llm_optimizer" / "config" / "parameter_registry.json"

# 以脚本方式启动时Python默认只把Agent交互界面加入模块路径，显式加入项目根目录。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_loader import load_agent_config, load_project_config, stamp_state_config, state_config_sync_status
from data_admission import build_admission_batch, latest_admission_manifest
from history_manager import cleanup_eligible_tasks, finalize_task, history_overview
from llm_optimizer.candidate_executor import formal_baseline_summaries, split_name
from llm_optimizer.experience_memory import build_round_experience, consolidate_round_state
from llm_optimizer.parameter_space import baseline_parameters, load_registry
from llm_optimizer.state_store import create_initial_state
from runtime_paths import ensure_f_drive_for_mutable_paths, load_runtime_paths


RUNTIME_PATHS = load_runtime_paths()
OUTPUT_ROOT = RUNTIME_PATHS["output_root"] / "LLM参数优化Agent"
FORMAL_RESULT = RUNTIME_PATHS["formal_result_path"]
CARSIM_SOLVER = RUNTIME_PATHS["carsim_solver"]
RUNTIME_ROOT = RUNTIME_PATHS["runtime_root"] / "llm_optimizer" / "ui_jobs"


def prepare_subprocess_environment(source: dict[str, str]) -> dict[str, str]:
    """复制环境变量，并强制Python子进程用UTF-8输出中文日志。"""
    environment = source.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


def task_no_improvement_after_round(current: int, improved: bool) -> int:
    """计算本次点击任务内的连续无提升轮数；历史状态只用于经验，不带入本次计数。"""
    return 0 if improved else current + 1


def read_json(path: Path) -> dict[str, Any]:
    """读取UTF-8 JSON；文件不存在时由调用者决定如何处理。"""
    return json.loads(path.read_text(encoding="utf-8"))


def write_round_state(folder: Path, state: dict[str, Any], experience: dict[str, Any]) -> Path:
    """把一轮全部候选汇总为唯一状态目录，供下一轮和下次任务继续读取。"""
    if folder.exists():
        raise FileExistsError(f"轮次经验目录已存在，拒绝覆盖：{folder}")
    folder.mkdir(parents=True)
    state_path = folder / "agent_state.json"
    temporary = state_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(state_path)
    (folder / "round_experience.json").write_text(
        json.dumps(experience, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (folder / "README.md").write_text(
        "# 多候选轮次经验状态\n\n"
        "本目录汇总同一轮C1/C2/C3的CarSim评价，不包含新的仿真结果。\n\n"
        "- `agent_state.json`：本轮唯一续跑状态，保留最优参数与经验记忆；\n"
        "- `round_experience.json`：候选参数变化、指标改善/退化、回退原因和本轮胜者。\n\n"
        "下一轮必须从本目录的状态继续，融合候选仍需重新通过CarSim验证。\n",
        encoding="utf-8",
    )
    return state_path


def api_config_status() -> dict[str, Any]:
    """只返回配置状态和非敏感字段，绝不把密钥内容传给前端。"""
    try:
        config = read_json(CONFIG_PATH)
    except (FileNotFoundError, json.JSONDecodeError) as error:
        return {"configured": False, "valid_json": False, "path": str(CONFIG_PATH), "message": str(error)}
    api_key = str(config.get("api_key", "")).strip()
    base_url = str(config.get("base_url", "")).strip()
    model = str(config.get("model", "")).strip()
    try:
        timeout_s = float(config.get("timeout_s", 120.0))
    except (TypeError, ValueError):
        timeout_s = 0.0
    complete = bool(api_key and base_url and model and base_url.startswith(("http://", "https://")) and timeout_s > 0)
    return {
        "configured": complete,
        "valid_json": True,
        "path": str(CONFIG_PATH),
        "base_url": base_url,
        "model": model,
        "timeout_s": timeout_s,
        "api_key_set": bool(api_key),
        "message": "配置完整" if complete else "请填写API Key、接口地址和模型名称",
    }


def load_private_api_config() -> dict[str, Any]:
    """仅在后端启动真实任务时读取密钥。"""
    status = api_config_status()
    if not status["configured"]:
        raise RuntimeError(status["message"])
    return read_json(CONFIG_PATH)


def latest_evaluation_state() -> Path | None:
    """选择最近完成的CarSim评价状态；没有历史输出时返回None。"""
    candidates = list(OUTPUT_ROOT.glob("*carsim_eval/agent_state.json"))
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def load_formal_result() -> dict[str, Any]:
    """读取冷启动所需正式基线，并在缺失时给出可执行提示。"""
    try:
        return read_json(FORMAL_RESULT)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"缺少正式联合基线：{FORMAL_RESULT}；请先重新计算当前配置正式基线"
        ) from error


def create_formal_baseline_state(
    formal: dict[str, Any], project_config: dict[str, Any], agent_config: dict[str, Any],
) -> dict[str, Any]:
    """按config.json当前样本划分构造首次运行状态，不依赖优化历史目录。"""
    summary = formal_baseline_summaries(formal, project_config, agent_config)["all_data"]
    state = create_initial_state(baseline_parameters(load_registry()), summary)
    return stamp_state_config(state, project_config)


def load_start_state(
    formal: dict[str, Any], project_config: dict[str, Any], agent_config: dict[str, Any], memory_mode: str = "inherit",
) -> tuple[Path | None, dict[str, Any]]:
    """按记忆模式续跑；口径变化时自动冷启动并隔离旧经验。"""
    if memory_mode not in {"inherit", "fresh"}:
        raise ValueError("memory_mode必须为inherit或fresh")
    if memory_mode == "fresh":
        return None, create_formal_baseline_state(formal, project_config, agent_config)
    state_path = latest_evaluation_state()
    if state_path is not None:
        state = read_json(state_path)
        if state_config_sync_status(state, project_config)["status"] != "stale":
            return state_path, state
    return None, create_formal_baseline_state(formal, project_config, agent_config)


def admission_payload() -> dict[str, Any]:
    """返回最新准入批次及各工况可优化性，不读取或返回原始数据正文。"""
    path = latest_admission_manifest()
    if path is None:
        return {"available": False, "ready_for_optimization": False, "message": "尚未生成数据准入批次"}
    manifest = read_json(path)
    minimum = load_project_config()["data_admission"]["minimum_samples"]
    by_role: dict[str, dict[str, int | bool]] = {}
    for record in manifest.get("records", []):
        role = str(record["role"])
        role_state = by_role.setdefault(role, {"accepted": 0, "rejected": 0, "pending_review": 0, "calibration": 0, "validation": 0})
        status = str(record["status"])
        role_state[status] = int(role_state.get(status, 0)) + 1
        if status == "accepted":
            split = str(record.get("dataset_split"))
            role_state[split] = int(role_state.get(split, 0)) + 1
    for role_state in by_role.values():
        role_state["ready"] = (
            int(role_state["calibration"]) >= int(minimum["calibration"])
            and int(role_state["validation"]) >= int(minimum["validation"])
        )
    ready = bool(by_role) and all(bool(item["ready"]) for item in by_role.values())
    return {
        "available": True, "ready_for_optimization": ready, "path": str(path),
        "batch_id": manifest.get("batch_id"), "counts": manifest.get("counts", {}),
        "data_fingerprint": manifest.get("data_fingerprint"), "by_role": by_role,
    }


def full_optimization_preflight() -> None:
    """真实闭环启动前检查CarSim和数据准入，干运行不受这些条件限制。"""
    missing = []
    try:
        ensure_f_drive_for_mutable_paths(RUNTIME_PATHS)
    except ValueError as error:
        missing.append(str(error))
    if not CARSIM_SOLVER.exists():
        missing.append(f"CarSim求解器：{CARSIM_SOLVER}")
    if not RUNTIME_PATHS["carsim_dll"].exists():
        missing.append(f"CarSim动态库：{RUNTIME_PATHS['carsim_dll']}")
    if not RUNTIME_PATHS["model_template_path"].exists():
        missing.append(f"车辆模型模板：{RUNTIME_PATHS['model_template_path']}")
    if RUNTIME_PATHS["formal_result_is_demo"]:
        missing.append("当前使用公开演示基线，请先生成并配置本车正式基线")
    admission = admission_payload()
    if load_project_config()["data_admission"].get("enforce_for_full_optimization", True) and not admission["ready_for_optimization"]:
        missing.append("合格实车数据不足，请先完成数据准入和人工复核")
    if missing:
        raise RuntimeError("完整优化环境未就绪：" + "；".join(missing))


def degrade_memory_after_failures(state: dict[str, Any], failure_count: int, threshold: int) -> bool:
    """连续失败达到阈值时仅保留本任务近期经验，停止继续注入更旧跨任务经验。"""
    if failure_count < threshold:
        return False
    rounds = state.get("optimization_memory", {}).get("rounds", [])
    state.get("optimization_memory", {})["rounds"] = rounds[-threshold:]
    state["cross_run_memory_degraded"] = True
    return True


def proposal_command(output: Path, use_api: bool, state_path: Path | None) -> list[str]:
    """构造候选生成命令；首次运行不传--state，由Agent自行建立初始状态。"""
    command = [
        sys.executable, "-m", "llm_optimizer.run_agent",
        "--formal-result", str(FORMAL_RESULT), "--output", str(output),
    ]
    if state_path is not None:
        command.extend(["--state", str(state_path)])
    command.append("--use-api" if use_api else "--dry-run")
    return command


def metric_target_pct(metric_name: str, thresholds: dict[str, Any]) -> float:
    """把评价器使用的原始阈值转换为看板显示的百分制目标。"""
    if metric_name == "speed_r2":
        return float(thresholds["speed_r2_min"]) * 100.0
    if metric_name == "speed_nrmse":
        return (1.0 - float(thresholds["speed_nrmse_max"])) * 100.0
    mapping = {
        "peak_ax": "peak_ax_accuracy_min_pct",
        "target_time": "target_time_accuracy_min_pct",
        "coasting_distance": "coasting_distance_accuracy_min_pct",
    }
    return float(thresholds[mapping[metric_name]])


def metric_rows(summary: dict[str, Any], thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    """把按工况求平均后的指标转换为前端表格数据。"""
    labels = {
        "zero_to_100": "0-100加速",
        "overtaking": "60-100超越",
        "coasting": "50-30滑行",
        "speed_r2": "车速 R2",
        "speed_nrmse": "车速 NRMSE",
        "peak_ax": "峰值加速度",
        "target_time": "目标时间",
        "coasting_distance": "滑行距离",
    }
    output = []
    for role, metrics in summary.get("mean_metric_scores_pct", {}).items():
        for name, score in metrics.items():
            target = metric_target_pct(name, thresholds)
            output.append({
                "role": role,
                "role_label": labels[role],
                "metric": name,
                "metric_label": labels[name],
                "score_pct": float(score),
                "target_pct": target,
                "passed": float(score) >= target,
            })
    return output


def metric_check_stats(summary: dict[str, Any], project_config: dict[str, Any]) -> dict[str, int]:
    """计算逐样本指标检查总数，避免与工况均值指标数量混为一谈。"""
    explicit_total = summary.get("metric_check_count")
    if explicit_total is not None:
        total = int(explicit_total)
    else:
        total = 0
        splits = project_config["agent"]["dataset_splits"]
        for role, metrics in summary.get("mean_metric_scores_pct", {}).items():
            split = splits.get(role, {})
            repeat_count = len(set(split.get("calibration", []) + split.get("validation", [])))
            total += repeat_count * len(metrics)
    failed = int(summary.get("failed_metric_count", 0))
    return {"total": total, "passed": max(0, total - failed), "failed": failed}


def find_best_evaluation_root(best: dict[str, Any], fallback_state_path: Path) -> Path:
    """定位当前最优候选对应的完整评价目录，避免误读最近一次已回退候选。"""
    expected_parameters = best.get("parameters", {})
    expected_score = float(best.get("summary", {}).get("longitudinal_score_pct", 0.0))
    matches: list[Path] = []
    for folder in OUTPUT_ROOT.glob("*carsim_eval"):
        decision_path = folder / "acceptance_decision.json"
        parameter_path = folder / "candidate_parameters.json"
        if not decision_path.exists() or not parameter_path.exists():
            continue
        try:
            decision = read_json(decision_path)
            parameters = read_json(parameter_path)
            candidate = decision.get("summaries", {}).get("all_data", {}).get("candidate", {})
            score = float(candidate.get("longitudinal_score_pct", -1.0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        same_parameters = all(
            name in parameters and abs(float(parameters[name]) - float(value)) <= 1e-9
            for name, value in expected_parameters.items()
        )
        if decision.get("accepted") and same_parameters and abs(score - expected_score) <= 1e-6:
            matches.append(folder)
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else fallback_state_path.parent


def failed_metric_details(
    evaluation_payloads: list[dict[str, Any]], thresholds: dict[str, Any],
) -> list[dict[str, Any]]:
    """把逐样本评价载荷转换为前端可直接解释的失败记录。"""
    role_labels = {"zero_to_100": "0-100加速", "overtaking": "60-100超越", "coasting": "50-30滑行"}
    metric_labels = {
        "speed_r2": "车速 R²", "speed_nrmse": "车速 NRMSE", "peak_ax": "峰值加速度",
        "target_time": "目标时间", "coasting_distance": "滑行距离",
    }
    details: list[dict[str, Any]] = []
    for payload in evaluation_payloads:
        comparisons = payload.get("comparisons", [])
        if payload.get("comparison"):
            comparisons = [payload["comparison"]]
        for comparison in comparisons:
            for metric_name, metric in comparison.get("metrics", {}).items():
                target = metric_target_pct(metric_name, thresholds)
                score = float(metric.get("score_pct", 0.0))
                if score >= target:
                    continue
                details.append({
                    "role": comparison.get("role"),
                    "role_label": role_labels.get(comparison.get("role"), comparison.get("role", "未知工况")),
                    "metric": metric_name,
                    "metric_label": metric_labels.get(metric_name, metric_name),
                    "repeat_index": comparison.get("repeat_index"),
                    "dataset_split": comparison.get("dataset_split", "未分组"),
                    "dataset_split_label": {
                        "calibration": "标定集", "validation": "验证集",
                    }.get(comparison.get("dataset_split"), "未分组"),
                    "score_pct": score,
                    "target_pct": target,
                })
    return details


def load_failed_metric_details(evaluation_root: Path, thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    """读取当前最优候选的逐条评价，列出每个实际未通过的指标。"""
    evaluation_payloads: list[dict[str, Any]] = []
    for path in evaluation_root.glob("calibration/*/repeat_*/evaluation.json"):
        evaluation_payloads.append(read_json(path))
    for path in evaluation_root.glob("validation/*/repeat_*/evaluation.json"):
        evaluation_payloads.append(read_json(path))
    coast_path = evaluation_root / "shared_simulation" / "coasting" / "evaluation_all_repeats.json"
    if coast_path.exists():
        evaluation_payloads.append(read_json(coast_path))
    return failed_metric_details(evaluation_payloads, thresholds)


def load_formal_failed_metric_details(
    formal: dict[str, Any], agent_config: dict[str, Any], thresholds: dict[str, Any],
) -> list[dict[str, Any]]:
    """无优化历史时，按当前样本划分展示正式基线的失败单项。"""
    comparisons: list[dict[str, Any]] = []
    for role in ("zero_to_100", "overtaking", "coasting"):
        role_results = [item for item in formal["results"] if item["role"] == role]
        for repeat_index, source in enumerate(role_results, start=1):
            split = split_name(repeat_index, agent_config, role)
            if split == "excluded":
                continue
            comparison = dict(source)
            comparison.update({"repeat_index": repeat_index, "dataset_split": split})
            comparisons.append(comparison)
    return failed_metric_details([{"comparisons": comparisons}], thresholds)


def apply_runtime_acceptance_threshold(summary: dict[str, Any], project_config: dict[str, Any]) -> dict[str, Any]:
    """用当前config.json重新计算看板上的正式阈值状态，不修改历史评价文件。"""
    normalized = dict(summary)
    threshold = float(project_config["formal_acceptance_threshold_pct"])
    normalized["formal_acceptance_threshold_pct"] = threshold
    total = float(normalized.get("longitudinal_score_pct", 0.0))
    normalized["formal_passed"] = bool(normalized.get("data_complete")) and total >= threshold
    normalized["pass_80_pct"] = total >= 80.0
    normalized["pass_85_pct"] = total >= 85.0
    return normalized


def iteration_history() -> list[dict[str, Any]]:
    """扫描分类输出目录，生成不依赖数据库的迭代历史。"""
    history = []
    if not OUTPUT_ROOT.exists():
        return history
    for folder in sorted((path for path in OUTPUT_ROOT.iterdir() if path.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True):
        item: dict[str, Any] = {
            "name": folder.name,
            "updated_at": datetime.fromtimestamp(folder.stat().st_mtime).isoformat(timespec="seconds"),
            "path": str(folder),
            "status": "已归档",
            "score_pct": None,
        }
        decision_path = folder / "acceptance_decision.json"
        state_path = folder / "agent_state.json"
        if decision_path.exists():
            decision = read_json(decision_path)
            item["status"] = "已提升" if decision.get("accepted") else "已回退"
        elif "dry_run" in folder.name:
            item["status"] = "干运行"
        elif "proposal" in folder.name:
            item["status"] = "等待评价"
        elif "round_memory" in folder.name:
            item["status"] = "经验已汇总"
        if state_path.exists():
            state = read_json(state_path)
            item["score_pct"] = state.get("best", {}).get("summary", {}).get("longitudinal_score_pct")
        history.append(item)
    return history[:12]


def dashboard_payload() -> dict[str, Any]:
    """汇总正式基线、当前最优参数、系统状态和迭代历史。"""
    project_config = load_project_config()
    agent_config = load_agent_config()
    formal = load_formal_result()
    state_path, state = load_start_state(formal, project_config, agent_config)
    best = state["best"]
    memory = state.get("optimization_memory", {})
    config_sync = state_config_sync_status(state, project_config)
    # 数据策略迁移后优先显示同口径正式基线，避免4份滑行与旧6份滑行直接比较。
    current_policy_baseline = create_formal_baseline_state(
        formal, project_config, agent_config,
    )["best"]["summary"]
    display_baseline = apply_runtime_acceptance_threshold(
        state.get("data_policy", {}).get("formal_baseline_summary", current_policy_baseline), project_config,
    )
    display_current = apply_runtime_acceptance_threshold(best["summary"], project_config)
    if state_path is None or best["source"] == "formal_baseline_current_config":
        failed_details = load_formal_failed_metric_details(
            formal, agent_config, project_config["metric_thresholds"],
        )
    else:
        best_evaluation_root = find_best_evaluation_root(best, state_path)
        failed_details = load_failed_metric_details(best_evaluation_root, project_config["metric_thresholds"])
    registry = read_json(REGISTRY_PATH)
    parameters = []
    for name, value in best["parameters"].items():
        spec = registry["parameters"][name]
        parameters.append({
            "name": name,
            "label": spec["label_zh"],
            "value": value,
            "unit": spec["unit"],
            "minimum": spec["minimum"],
            "maximum": spec["maximum"],
            "baseline": spec["baseline"],
        })
    changed_parameters = parameter_change_summary(best["parameters"], registry)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "api": api_config_status(),
        "system": {
            "carsim_ready": CARSIM_SOLVER.exists(),
            "demo_mode": bool(RUNTIME_PATHS["formal_result_is_demo"]),
            "python_version": sys.version.split()[0],
            "state_path": str(state_path) if state_path is not None else None,
            "state_source": "evaluation_history" if state_path is not None else "formal_baseline",
            "project_root": str(PROJECT_ROOT),
        },
        "scores": {
            "baseline": display_baseline,
            "current": display_current,
            "metric_checks": {
                "baseline": metric_check_stats(display_baseline, project_config),
                "current": metric_check_stats(display_current, project_config),
            },
            "target_pct": float(agent_config["optimization_target_pct"]),
            "chart": project_config["chart_axis"],
        },
        "metrics": metric_rows(display_current, project_config["metric_thresholds"]),
        "failed_details": failed_details,
        "parameters": parameters,
        "history": iteration_history(),
        "agent": {
            "best_source": best["source"],
            "iteration": state.get("current_iteration", 0),
            "no_improvement_iterations": state.get("no_improvement_iterations", 0),
            # 将循环上限和停止耐心值一并返回，前端只展示后端 config.json 的真实口径。
            "maximum_iterations": int(agent_config["maximum_iterations"]),
            "stop_after_no_improvement_iterations": int(agent_config.get("stop_after_no_improvement_iterations", 3)),
            "memory_version": memory.get("version"),
            "memory_rounds": len(memory.get("rounds", [])),
            "configured_formal_threshold_pct": float(project_config["formal_acceptance_threshold_pct"]),
            "state_path": str(state_path) if state_path is not None else None,
            "config_sync": config_sync,
            "parameter_change_summary": changed_parameters,
        },
    }


def parameter_change_summary(parameters: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    """汇总当前最优点相对正式基线的真实参数变化，替代固定展示某一个参数。"""
    changes = []
    for name, specification in registry["parameters"].items():
        if name not in parameters:
            continue
        baseline = float(specification["baseline"])
        current = float(parameters[name])
        if abs(current - baseline) <= 1e-12:
            continue
        changes.append({
            "name": name,
            "label": str(specification["label_zh"]),
            "baseline": baseline,
            "current": current,
            "unit": str(specification.get("unit", "")),
        })
    if not changes:
        return {"count": 0, "text": "正式基线", "details": []}
    text = (
        f"{changes[0]['label']} {changes[0]['baseline']:g}→{changes[0]['current']:g}"
        if len(changes) == 1 else f"已调整 {len(changes)} 项"
    )
    return {"count": len(changes), "text": text, "details": changes}


class JobManager:
    """单任务异步执行器，防止重复点击并发修改同一Agent状态。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.pause_requested = False
        self.stop_requested = False
        self.state: dict[str, Any] = {
            "status": "idle", "mode": None, "started_at": None, "finished_at": None,
            "logs": [], "result": None, "error": None, "progress": self.empty_progress(),
        }

    @staticmethod
    def empty_progress() -> dict[str, Any]:
        """创建前端过程看板使用的结构化进度状态。"""
        return {
            "current_round": 0, "max_rounds": 0, "phase": "idle", "phase_label": "等待启动",
            "candidate_total": 0, "candidate_completed": 0, "candidates": [],
            "best_score_pct": None, "last_score_pct": None, "last_candidate_id": None,
            "last_decision": None, "no_improvement_rounds": 0,
        }

    def snapshot(self) -> dict[str, Any]:
        """返回可序列化副本，限制日志长度避免前端负担过大。"""
        with self.lock:
            snapshot = dict(self.state)
            snapshot["logs"] = list(self.state["logs"][-200:])
            return snapshot

    def append_log(self, text: str) -> None:
        """记录一行不含密钥的子进程输出。"""
        with self.lock:
            self.state["logs"].append(text.rstrip())

    def update_progress(self, **updates: Any) -> None:
        """原子更新结构化进度，避免前端只能解析原始控制台文本。"""
        with self.lock:
            self.state["progress"].update(updates)

    def start(self, mode: str, memory_mode: str = "inherit") -> None:
        """启动干运行或真实API完整迭代。"""
        with self.lock:
            if self.state["status"] in {"running", "pausing", "paused", "stopping"}:
                raise RuntimeError("已有优化任务正在运行")
            self.pause_requested = False
            self.stop_requested = False
            self.state = {
                "status": "running", "mode": mode, "memory_mode": memory_mode,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": None, "logs": [], "result": None, "error": None,
                "progress": self.empty_progress(),
            }
        threading.Thread(target=self._run, args=(mode, memory_mode), daemon=True).start()

    def request_pause(self) -> None:
        """请求在当前外部命令完成后的安全边界暂停。"""
        with self.condition:
            if self.state.get("mode") != "full_iteration":
                raise RuntimeError("只有完整优化任务支持暂停")
            if self.state["status"] != "running":
                raise RuntimeError("当前任务不处于可暂停的运行状态")
            self.pause_requested = True
            self.state["status"] = "pausing"
            self.state["logs"].append("已请求安全暂停，将在当前候选或模型调用结束后暂停")

    def resume(self) -> None:
        """取消暂停请求或唤醒已暂停的工作线程。"""
        with self.condition:
            if self.state["status"] not in {"pausing", "paused"}:
                raise RuntimeError("当前任务没有处于暂停状态")
            self.pause_requested = False
            self.state["status"] = "running"
            self.state["logs"].append("优化任务已继续")
            self.condition.notify_all()

    def request_stop(self) -> None:
        """请求在当前外部命令完成后安全停止，并唤醒可能暂停的线程。"""
        with self.condition:
            if self.state["status"] not in {"running", "pausing", "paused"}:
                raise RuntimeError("当前没有可停止的优化任务")
            self.stop_requested = True
            self.pause_requested = False
            self.state["status"] = "stopping"
            self.state["logs"].append("已请求安全停止，将保留当前最优点和已完成候选")
            self.condition.notify_all()

    def wait_for_control_boundary(self, resume_label: str) -> bool:
        """在候选边界响应暂停/停止；返回False表示应结束任务。"""
        with self.condition:
            if self.stop_requested:
                return False
            if not self.pause_requested:
                return True
            self.state["status"] = "paused"
            self.state["progress"]["phase"] = "paused"
            self.state["progress"]["phase_label"] = "优化已暂停"
            self.state["logs"].append("优化已在安全边界暂停")
            while self.pause_requested and not self.stop_requested:
                self.condition.wait()
            if self.stop_requested:
                return False
            self.state["status"] = "running"
            self.state["progress"]["phase"] = "running"
            self.state["progress"]["phase_label"] = resume_label
            return True

    def _run_command(self, command: list[str], env: dict[str, str]) -> None:
        """逐行读取子进程输出，Windows下不弹出额外命令窗口。"""
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            command, cwd=PROJECT_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=flags,
        )
        assert process.stdout is not None
        recent_lines: list[str] = []
        for line in process.stdout:
            self.append_log(line)
            stripped = line.strip()
            if stripped:
                recent_lines.append(stripped)
                recent_lines = recent_lines[-20:]
        return_code = process.wait()
        if return_code != 0:
            # 优先把子进程最后的RuntimeError带到任务状态，用户无需翻完整Traceback寻找原因。
            actionable = next(
                (line for line in reversed(recent_lines) if line.startswith(("RuntimeError:", "ValueError:"))),
                None,
            )
            detail = f"；{actionable}" if actionable else ""
            raise RuntimeError(f"命令执行失败，返回码={return_code}{detail}")

    def _run(self, mode: str, memory_mode: str) -> None:
        """执行干运行或多轮LLM-CarSim闭环，并在每轮后更新状态路径。"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            project_config = load_project_config()
            agent_config = load_agent_config()
            formal = load_formal_result()
            state_path, start_state = load_start_state(formal, project_config, agent_config, memory_mode)
            env = prepare_subprocess_environment(os.environ)
            if mode == "dry_run":
                self.update_progress(phase="proposal", phase_label="生成候选", max_rounds=0)
                proposal = OUTPUT_ROOT / f"ui_{timestamp}_dry_run"
                self.append_log("开始生成参数候选（干运行，不进入CarSim）")
                self._run_command(proposal_command(proposal, False, state_path), env)
                # 干运行也展示安全校验后的候选数量，并明确标记任务已结束。
                validation = read_json(proposal / "candidate_validation.json")
                accepted = validation.get("accepted", [])
                self.update_progress(
                    candidate_total=len(accepted),
                    candidate_completed=len(accepted),
                    candidates=[{
                        "candidate_id": item.get("candidate_id"),
                        "status": "已生成",
                        "score_pct": None,
                    } for item in accepted],
                    phase="completed",
                    phase_label="优化完成（干运行）",
                )
                result = {"proposal": str(proposal), "evaluation": None, "iterations": 0}
            else:
                full_optimization_preflight()
                private = load_private_api_config()
                env["CARSIM_LLM_API_KEY"] = str(private["api_key"])
                env["CARSIM_LLM_BASE_URL"] = str(private["base_url"])
                env["CARSIM_LLM_MODEL"] = str(private["model"])
                env["CARSIM_LLM_TIMEOUT_S"] = str(private.get("timeout_s", 120.0))
                # 迭代上限由项目根config.json控制，避免前端与Agent各自维护一套上限。
                max_iterations = int(agent_config["maximum_iterations"])
                stop_after = int(agent_config.get("stop_after_no_improvement_iterations", 3))
                memory_fallback_after = int(project_config["experience_policy"].get("fallback_after_memory_failures", 2))
                self.update_progress(max_rounds=max_iterations, phase="starting", phase_label="准备闭环")
                evaluations: list[str] = []
                round_states: list[str] = []
                # 每次点击“开始完整优化”都是新的任务，停止耐心值从0开始；历史值仍保留在状态和提示词中。
                historical_no_improvement_rounds = int(start_state.get("no_improvement_iterations", 0))
                no_improvement_rounds = 0
                self.update_progress(best_score_pct=float(start_state["best"]["summary"]["longitudinal_score_pct"]))
                self.append_log(
                    f"本次任务连续无提升从0轮开始（继承历史经验：此前连续无提升{historical_no_improvement_rounds}轮）"
                )
                rounds_completed = 0
                stopped_early = False
                for iteration in range(1, max_iterations + 1):
                    if not self.wait_for_control_boundary("准备生成下一轮候选"):
                        stopped_early = True
                        break
                    self.update_progress(
                        current_round=iteration, phase="proposal", phase_label="生成参数候选",
                        candidate_total=0, candidate_completed=0, candidates=[],
                    )
                    proposal = OUTPUT_ROOT / f"ui_{timestamp}_iter_{iteration:02d}_llm_proposal"
                    self.append_log(f"开始第{iteration}/{max_iterations}轮生成参数候选")
                    self._run_command(proposal_command(proposal, True, state_path), env)
                    if not self.wait_for_control_boundary("处理本轮参数候选"):
                        stopped_early = True
                        break
                    validation = read_json(proposal / "candidate_validation.json")
                    accepted = validation.get("accepted", [])
                    self.update_progress(
                        phase="evaluation" if accepted else "memory", phase_label="运行 CarSim 评价" if accepted else "整理失败经验",
                        candidate_total=len(accepted), candidate_completed=0,
                        candidates=[{"candidate_id": item.get("candidate_id"), "status": "等待评价", "score_pct": None} for item in accepted],
                    )
                    if not accepted:
                        # 安全校验失败也是有价值的经验，持久化后让下一轮避免重复无效提案。
                        proposal_state = read_json(proposal / "agent_state.json")
                        round_experience = build_round_experience(
                            iteration, proposal_state, [], None, validation.get("rejected", []),
                        )
                        consolidated = consolidate_round_state(proposal_state, [], round_experience, None)
                        if memory_mode == "inherit" and degrade_memory_after_failures(consolidated, no_improvement_rounds + 1, memory_fallback_after):
                            self.append_log("历史经验连续引导失败，后续仅保留本任务近期经验")
                        round_folder = OUTPUT_ROOT / f"ui_{timestamp}_iter_{iteration:02d}_round_memory_carsim_eval"
                        state_path = write_round_state(round_folder, consolidated, round_experience)
                        round_states.append(str(round_folder))
                        no_improvement_rounds = task_no_improvement_after_round(no_improvement_rounds, False)
                        self.update_progress(no_improvement_rounds=no_improvement_rounds, phase="memory", phase_label="整理失败经验")
                        persisted_no_improvement_rounds = int(consolidated["no_improvement_iterations"])
                        rounds_completed += 1
                        self.append_log(
                            f"第{iteration}轮没有候选通过安全校验，失败规则已写入经验记忆，"
                            f"本次任务连续无提升={no_improvement_rounds}轮（状态累计={persisted_no_improvement_rounds}轮）"
                        )
                        if no_improvement_rounds >= stop_after:
                            self.append_log(f"达到连续{stop_after}轮无提升阈值，自动停止循环")
                            break
                        continue
                    # 同一轮的所有候选都基于同一个state_path独立评价，避免候选之间互相污染。
                    round_results: list[dict[str, Any]] = []
                    for candidate in accepted:
                        if not self.wait_for_control_boundary("运行 CarSim 评价"):
                            stopped_early = True
                            break
                        candidate_id = str(candidate["candidate_id"])
                        safe_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in candidate_id)
                        evaluation = OUTPUT_ROOT / f"ui_{timestamp}_iter_{iteration:02d}_{safe_id}_carsim_eval"
                        runtime = RUNTIME_ROOT / f"{timestamp}_iter_{iteration:02d}_{safe_id}"
                        self.append_log(f"第{iteration}轮共{len(accepted)}个候选，开始评价{candidate_id}")
                        self._run_command([
                            sys.executable, "-m", "llm_optimizer.candidate_executor",
                            "--proposal-root", str(proposal), "--output", str(evaluation), "--runtime", str(runtime),
                            "--candidate-id", candidate_id,
                        ], env)
                        decision = read_json(evaluation / "acceptance_decision.json")
                        candidate_state = read_json(evaluation / "agent_state.json")
                        round_results.append({
                            "path": evaluation,
                            "candidate_id": candidate_id,
                            "decision": decision,
                            "state": candidate_state,
                        })
                        evaluations.append(str(evaluation))
                        summary = decision["summaries"]["all_data"]["candidate"]
                        candidate_snapshot = {
                            "candidate_id": candidate_id,
                            "status": "已接受" if decision.get("accepted") else "已回退",
                            "score_pct": float(summary["longitudinal_score_pct"]),
                            "failed_metric_count": int(summary.get("failed_metric_count", 0)),
                        }
                        self.update_progress(
                            candidate_completed=len(round_results), last_candidate_id=candidate_id,
                            last_score_pct=candidate_snapshot["score_pct"], last_decision=candidate_snapshot["status"],
                            candidates=[candidate_snapshot if item["candidate_id"] == candidate_id else item for item in self.snapshot()["progress"]["candidates"]],
                        )
                        self.append_log(
                            f"候选{candidate_id}完成：综合{float(summary['longitudinal_score_pct']):.2f}%，"
                            f"{'通过' if decision.get('accepted') else '回退'}"
                        )

                    # 若尚无候选完成，停止时不把未评价候选误记成失败经验。
                    if stopped_early and not round_results:
                        break

                    accepted_results = [record for record in round_results if record["decision"].get("accepted")]
                    winner_candidate_id = None
                    if accepted_results:
                        # 先比较综合分，再比较未通过单项数，确定本轮唯一晋级者。
                        best_record = max(
                            accepted_results,
                            key=lambda record: (
                                float(record["decision"]["summaries"]["all_data"]["candidate"]["longitudinal_score_pct"]),
                                -int(record["decision"]["summaries"]["all_data"]["candidate"]["failed_metric_count"]),
                            ),
                        )
                        winner_candidate_id = str(best_record["candidate_id"])

                    # 无论有无胜者，都保存全部候选经验；下一轮只继承胜者参数，不直接拼接非胜者参数。
                    proposal_state = read_json(proposal / "agent_state.json")
                    round_experience = build_round_experience(
                        iteration, proposal_state, round_results, winner_candidate_id, validation.get("rejected", []),
                    )
                    consolidated = consolidate_round_state(
                        proposal_state, round_results, round_experience, winner_candidate_id,
                    )
                    if memory_mode == "inherit" and winner_candidate_id is None and degrade_memory_after_failures(
                        consolidated, no_improvement_rounds + 1, memory_fallback_after,
                    ):
                        self.append_log("历史经验连续引导失败，后续仅保留本任务近期经验")
                    round_folder = OUTPUT_ROOT / f"ui_{timestamp}_iter_{iteration:02d}_round_memory_carsim_eval"
                    state_path = write_round_state(round_folder, consolidated, round_experience)
                    round_states.append(str(round_folder))
                    no_improvement_rounds = task_no_improvement_after_round(
                        no_improvement_rounds, winner_candidate_id is not None,
                    )
                    persisted_no_improvement_rounds = int(consolidated["no_improvement_iterations"])
                    self.update_progress(
                        phase="round_summary", phase_label="本轮评价完成",
                        best_score_pct=float(consolidated["best"]["summary"]["longitudinal_score_pct"]),
                        no_improvement_rounds=no_improvement_rounds,
                    )
                    if winner_candidate_id is not None:
                        best_summary = consolidated["best"]["summary"]
                        self.append_log(
                            f"第{iteration}轮候选比较完成，晋级{winner_candidate_id}；其他候选经验已保留，"
                            f"当前最优{float(best_summary['longitudinal_score_pct']):.2f}%"
                        )
                    else:
                        self.append_log(
                            f"第{iteration}轮所有候选均回退，失败方向已写入下一轮提示词，"
                            f"本次任务连续无提升={no_improvement_rounds}轮（状态累计={persisted_no_improvement_rounds}轮）"
                        )
                    rounds_completed += 1
                    if stopped_early:
                        self.append_log("已完成候选结果归档，任务按用户请求安全停止")
                        break
                    if no_improvement_rounds >= stop_after:
                        self.append_log(f"达到连续{stop_after}轮无提升阈值，自动停止循环")
                        break
                result = {
                    "proposal": None,
                    "evaluation": evaluations[-1] if evaluations else None,
                    "evaluations": evaluations,
                    "round_states": round_states,
                    "iterations": rounds_completed,
                    "candidate_evaluations": len(evaluations),
                    "task_no_improvement_rounds": no_improvement_rounds,
                    "historical_no_improvement_rounds": historical_no_improvement_rounds,
                    "memory_mode": memory_mode,
                    "stopped_by_user": stopped_early,
                }
                task_id = f"ui_{timestamp}"
                result["task_archive"] = str(
                    OUTPUT_ROOT / "任务档案" / task_id / "task_summary.json"
                )
                finalize_task(task_id, result)
                self.update_progress(
                    phase="stopped" if stopped_early else "completed",
                    phase_label="已安全停止" if stopped_early else "优化完成",
                    no_improvement_rounds=no_improvement_rounds,
                )
            with self.lock:
                self.state["status"] = "stopped" if mode != "dry_run" and result.get("stopped_by_user") else "completed"
                self.state["finished_at"] = datetime.now().isoformat(timespec="seconds")
                self.state["result"] = result
        except Exception as error:  # 界面必须得到可读错误，同时保留当前最优基线。
            self.append_log(traceback.format_exc())
            self.update_progress(phase="failed", phase_label="任务失败")
            with self.lock:
                self.state["status"] = "failed"
                self.state["finished_at"] = datetime.now().isoformat(timespec="seconds")
                self.state["error"] = str(error)


JOBS = JobManager()


class AgentRequestHandler(BaseHTTPRequestHandler):
    """静态文件与JSON API请求处理器。"""

    server_version = "VectorTuneAgent/0.1"

    def log_message(self, format_text: str, *args: Any) -> None:
        """保留精简访问日志。"""
        print(f"[{self.log_date_time_string()}] {format_text % args}")

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        """返回UTF-8 JSON响应。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def parse_body(self) -> dict[str, Any]:
        """读取有限大小的JSON请求体。"""
        length = min(int(self.headers.get("Content-Length", "0")), 64 * 1024)
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self) -> None:
        """处理看板、任务状态和静态资源。"""
        path = urlparse(self.path).path
        try:
            if path == "/api/dashboard":
                self.send_json(dashboard_payload())
                return
            if path == "/api/job":
                self.send_json(JOBS.snapshot())
                return
            if path == "/api/history/storage":
                self.send_json(history_overview())
                return
            if path == "/api/data-admission":
                self.send_json(admission_payload())
                return
            self.serve_static(path)
        except Exception as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        """处理本地配置检查、打开配置文件和启动优化。"""
        path = urlparse(self.path).path
        try:
            if path == "/api/config/check":
                self.send_json(api_config_status())
                return
            if path == "/api/config/open":
                if os.name != "nt":
                    raise RuntimeError("当前系统不支持自动打开配置文件")
                os.startfile(CONFIG_PATH)  # type: ignore[attr-defined]
                self.send_json({"opened": True, "path": str(CONFIG_PATH)})
                return
            if path == "/api/jobs/start":
                body = self.parse_body()
                mode = body.get("mode")
                if mode not in {"dry_run", "full_iteration"}:
                    self.send_json({"error": "mode必须为dry_run或full_iteration"}, HTTPStatus.BAD_REQUEST)
                    return
                memory_mode = body.get("memory_mode", "inherit")
                if memory_mode not in {"inherit", "fresh"}:
                    self.send_json({"error": "memory_mode必须为inherit或fresh"}, HTTPStatus.BAD_REQUEST)
                    return
                JOBS.start(mode, memory_mode)
                self.send_json(JOBS.snapshot(), HTTPStatus.ACCEPTED)
                return
            if path == "/api/jobs/pause":
                JOBS.request_pause()
                self.send_json(JOBS.snapshot(), HTTPStatus.ACCEPTED)
                return
            if path == "/api/jobs/resume":
                JOBS.resume()
                self.send_json(JOBS.snapshot(), HTTPStatus.ACCEPTED)
                return
            if path == "/api/jobs/stop":
                JOBS.request_stop()
                self.send_json(JOBS.snapshot(), HTTPStatus.ACCEPTED)
                return
            if path == "/api/data-admission/run":
                manifest = build_admission_batch()
                self.send_json({"batch_id": manifest["batch_id"], "counts": manifest["counts"]}, HTTPStatus.CREATED)
                return
            if path == "/api/history/cleanup":
                body = self.parse_body()
                result = cleanup_eligible_tasks(str(body.get("confirm", "")), body.get("task_ids"))
                self.send_json(result)
                return
            self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except RuntimeError as error:
            self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
        except Exception as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def serve_static(self, request_path: str) -> None:
        """仅允许读取static目录内文件，阻止路径穿越。"""
        relative = "index.html" if request_path in {"", "/"} else unquote(request_path.lstrip("/"))
        target = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT.resolve() not in target.parents and target != STATIC_ROOT.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        payload = target.read_bytes()
        mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type + ("; charset=utf-8" if mime_type.startswith("text/") or mime_type == "application/javascript" else ""))
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    """启动只允许本机访问的交互服务。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise ValueError("为保护API密钥，服务仅允许监听127.0.0.1或localhost")
    server = ThreadingHTTPServer((args.host, args.port), AgentRequestHandler)
    print(f"Agent UI: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
