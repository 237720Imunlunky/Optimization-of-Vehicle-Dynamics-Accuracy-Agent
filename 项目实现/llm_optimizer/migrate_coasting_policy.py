"""把当前最优状态迁移到严格50->30滑行样本策略，不重复运行CarSim。"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from .candidate_executor import evaluation_summary, read_json, split_formal_baseline, split_name, write_json
from .parameter_space import load_agent_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_CONFIG = PROJECT_ROOT / "config.json"
FORMAL_RESULT = PROJECT_ROOT / "输出" / "正式联合基线" / "当前配置基线" / "formal_acceptance.json"


def load_archived_results(evidence_root: Path, agent_config: dict[str, Any]) -> list[dict[str, Any]]:
    """读取当前最优候选的逐次归档结果，并应用新版工况专属数据划分。"""
    results: list[dict[str, Any]] = []
    for split in ("calibration", "validation"):
        for role in ("zero_to_100", "overtaking"):
            for evaluation_path in sorted((evidence_root / split / role).glob("repeat_*/evaluation.json")):
                payload = read_json(evaluation_path)
                comparison = payload["comparison"]
                repeat_index = int(comparison["repeat_index"])
                comparison["dataset_split"] = split_name(repeat_index, agent_config, role)
                results.append(comparison)

    coasting = read_json(evidence_root / "shared_simulation" / "coasting" / "evaluation_all_repeats.json")
    for comparison in coasting["comparisons"]:
        repeat_index = int(comparison["repeat_index"])
        split = split_name(repeat_index, agent_config, "coasting")
        if split == "excluded":
            continue
        comparison["dataset_split"] = split
        results.append(comparison)
    return results


def summarize_by_split(results: list[dict[str, Any]], project_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """按标定集、验证集和全部数据生成同口径摘要。"""
    return {
        "calibration": evaluation_summary([item for item in results if item["dataset_split"] == "calibration"], project_config),
        "validation": evaluation_summary([item for item in results if item["dataset_split"] == "validation"], project_config),
        "all_data": evaluation_summary(results, project_config),
    }


def migrate_state(state: dict[str, Any], summaries: dict[str, dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    """更新当前最优摘要和其最近接受记录，同时完整保留历史。"""
    migrated = copy.deepcopy(state)
    source = migrated["best"]["source"]
    migrated["best"]["summary"] = summaries["all_data"]
    for entry in reversed(migrated.get("history", [])):
        if entry.get("status") == "accepted" and entry.get("candidate_id") == source:
            for split, summary in summaries.items():
                entry.setdefault("decision", {}).setdefault("summaries", {}).setdefault(split, {})["candidate"] = summary
            break
    else:
        raise ValueError("当前最优状态中未找到对应的已接受候选记录")
    migrated["data_policy"] = policy
    return migrated


def build_formal_baseline(formal: dict[str, Any], agent_config: dict[str, Any], project_config: dict[str, Any]) -> dict[str, Any]:
    """对正式基线应用相同样本排除规则，供看板同口径展示。"""
    groups = split_formal_baseline(formal, agent_config)
    return evaluation_summary(groups["calibration"] + groups["validation"], project_config)


def main() -> None:
    """执行一次可追溯迁移，输出新状态、摘要和说明文件。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="待迁移的最新Agent状态")
    parser.add_argument("--evidence", type=Path, required=True, help="当前最优候选的CarSim评价证据目录")
    parser.add_argument("--output", type=Path, required=True, help="独立迁移输出目录")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output}")
    output.mkdir(parents=True)

    agent_config = load_agent_config()
    project_config = read_json(PROJECT_CONFIG)
    formal = read_json(FORMAL_RESULT)
    results = load_archived_results(args.evidence.resolve(), agent_config)
    summaries = summarize_by_split(results, project_config)
    policy = {
        "version": agent_config["coasting_test_condition"]["policy_version"],
        "coasting_condition": agent_config["coasting_test_condition"],
        "dataset_splits": agent_config["dataset_splits"],
        "formal_baseline_summary": build_formal_baseline(formal, agent_config, project_config),
        "source_evidence": str(args.evidence.resolve()),
    }
    state = migrate_state(read_json(args.state.resolve()), summaries, policy)
    write_json(output / "agent_state.json", state)
    write_json(output / "recalculated_summaries.json", summaries)
    write_json(output / "data_policy_manifest.json", policy)
    (output / "README.md").write_text(
        "# 严格50->30 N挡滑行数据策略迁移\n\n"
        "本目录没有重复运行CarSim，而是使用当前最优候选已归档的逐工况结果，"
        "排除滑行第3次（中途制动）和第6次（缺少50 km/h起点）后重新聚合。\n\n"
        "原始CAN已确认有效样本为舒适模式N挡、零油门、零制动和零回收，"
        "因此继续复用回收关闭的共享CarSim滑行结果，仅修正工况语义和审计证据。\n\n"
        "- `agent_state.json`：供后续Agent按新口径续跑；\n"
        "- `recalculated_summaries.json`：标定、验证和全部数据摘要；\n"
        "- `data_policy_manifest.json`：样本划分、试验条件和来源证据。\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "summary": summaries["all_data"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
