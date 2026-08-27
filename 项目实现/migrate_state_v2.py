"""把旧Agent状态复制迁移为有界历史V2，绝不覆盖原状态文件。"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from llm_optimizer.state_store import STATE_VERSION, compact_state_history
from runtime_paths import load_runtime_paths


def find_best_split_summaries(state: dict[str, Any]) -> dict[str, Any] | None:
    """从旧详细历史恢复最佳候选三层摘要，供历史裁剪后继续优化。"""
    source = state.get("best", {}).get("source")
    for entry in reversed(state.get("history", [])):
        summaries = entry.get("decision", {}).get("summaries", {})
        if entry.get("status") == "accepted" and entry.get("candidate_id") == source and all(
            name in summaries and "candidate" in summaries[name] for name in ("calibration", "validation", "all_data")
        ):
            return {name: summaries[name]["candidate"] for name in ("calibration", "validation", "all_data")}
    return None


def migrate_state(source: Path, output: Path) -> dict[str, Any]:
    """迁移单个状态并写入独立输出目录。"""
    state = json.loads(source.read_text(encoding="utf-8"))
    migrated = copy.deepcopy(state)
    summaries = find_best_split_summaries(migrated)
    if summaries and not migrated.get("best", {}).get("split_summaries"):
        migrated["best"]["split_summaries"] = summaries
    migrated["state_version"] = STATE_VERSION
    migrated["migration"] = {
        "source": str(source), "migrated_at": datetime.now().isoformat(timespec="seconds"),
        "source_unchanged": True,
    }
    compact_state_history(migrated)
    output.mkdir(parents=True, exist_ok=False)
    destination = output / "agent_state_v2.json"
    destination.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "README.md").write_text(
        "# Agent状态V2迁移结果\n\n`agent_state_v2.json`是旧状态的兼容副本，"
        "包含有界详细历史和最佳候选三层摘要。原状态文件未修改，确认新文件可用前请勿清理旧证据。\n",
        encoding="utf-8",
    )
    return migrated


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    timestamp = datetime.now().strftime("state_v2_%Y%m%d_%H%M%S")
    output = args.output or (load_runtime_paths()["output_root"] / "状态迁移" / timestamp)
    migrated = migrate_state(args.source.resolve(), output.resolve())
    print(json.dumps({"output": str(output.resolve()), "history_records": len(migrated["history"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
