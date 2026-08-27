"""保存Agent迭代状态，并确保未评价候选不能覆盖最优基线。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config_loader import load_project_config


STATE_VERSION = "2.0"


def compact_state_history(state: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    """限制详细历史长度，并把被裁剪记录汇总为永久审计计数。"""
    configured_limit = int(load_project_config()["history_retention"]["state_history_limit"])
    maximum = configured_limit if limit is None else int(limit)
    history = state.setdefault("history", [])
    overflow = max(0, len(history) - maximum)
    if overflow == 0:
        return state
    removed = history[:overflow]
    archive = state.setdefault("history_archive", {"compacted_records": 0, "status_counts": {}})
    archive["compacted_records"] = int(archive.get("compacted_records", 0)) + len(removed)
    counts = archive.setdefault("status_counts", {})
    for entry in removed:
        status = str(entry.get("status", "unknown"))
        counts[status] = int(counts.get(status, 0)) + 1
    archive["last_compacted_at"] = datetime.now().isoformat(timespec="seconds")
    state["history"] = history[overflow:]
    return state


def create_initial_state(parameters: dict[str, float], summary: dict[str, Any]) -> dict[str, Any]:
    """以当前配置的正式评价结果建立不可丢失的初始最优点。"""
    return {
        "state_version": STATE_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "current_iteration": 0,
        "no_improvement_iterations": 0,
        "best": {"source": "formal_baseline_current_config", "parameters": parameters, "summary": summary},
        "history": [],
    }


def record_proposal(state: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    """记录待仿真候选；此步骤绝不改变best，实现失败前自动回退。"""
    state["current_iteration"] += 1
    state["history"].append({
        "iteration": state["current_iteration"],
        "status": "awaiting_carsim_evaluation",
        "accepted_candidate_ids": [item["candidate_id"] for item in validation["accepted"]],
        "rejected": validation["rejected"],
    })
    return compact_state_history(state)


def record_evaluation(
    state: dict[str, Any],
    candidate_id: str,
    parameters: dict[str, float],
    summary: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """评价完成后按程序判定更新best；拒绝时自动保留原最优参数。"""
    entry = {
        "iteration": state["current_iteration"],
        "candidate_id": candidate_id,
        "status": "accepted" if decision["accepted"] else "rejected_and_rolled_back",
        "parameters": parameters,
        "summary": summary,
        "decision": decision,
    }
    state["history"].append(entry)
    if decision["accepted"]:
        state["best"] = {
            "source": candidate_id, "parameters": parameters, "summary": summary,
            "split_summaries": {
                name: decision["summaries"][name]["candidate"]
                for name in ("calibration", "validation", "all_data")
            },
        }
        state["no_improvement_iterations"] = 0
    else:
        state["no_improvement_iterations"] += 1
    return compact_state_history(state)



def write_state(path: Path, state: dict[str, Any]) -> None:
    """原子替换状态文件，避免中断时留下半截JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    state["state_version"] = STATE_VERSION
    compact_state_history(state)
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
