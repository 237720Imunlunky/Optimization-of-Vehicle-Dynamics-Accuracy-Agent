"""为当前同口径最优状态初始化多候选经验记忆。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .experience_memory import initialize_state_memory
from .state_store import write_state


def main() -> None:
    """建立独立迁移目录，不覆盖原状态或导入不兼容的旧评价经验。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="当前v4同口径Agent状态")
    parser.add_argument("--output", type=Path, required=True, help="新的经验记忆状态目录")
    args = parser.parse_args()
    source = args.state.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output}")
    output.mkdir(parents=True)
    state = json.loads(source.read_text(encoding="utf-8"))
    initialized = initialize_state_memory(state, str(source))
    write_state(output / "agent_state.json", initialized)
    (output / "experience_memory.json").write_text(
        json.dumps(initialized["optimization_memory"], ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (output / "README.md").write_text(
        "# 多候选经验记忆初始化\n\n"
        "本目录从严格50->30 N挡v4状态初始化经验库，不修改当前最优参数和分数。\n\n"
        "旧轮次采用6份滑行数据，和当前排除第3、6次后的4份有效样本口径不同，"
        "因此不把旧候选变化直接当作可融合经验。后续每轮将完整记录C1/C2/C3。\n\n"
        "- `agent_state.json`：后续Agent续跑入口；\n"
        "- `experience_memory.json`：空经验库及初始化来源。\n\n"
        "运行方式：`python -m llm_optimizer.migrate_experience_memory --state <v4状态> --output <新目录>`。\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(output),
        "policy_version": initialized["optimization_memory"]["policy_version"],
        "score_pct": initialized["best"]["summary"]["longitudinal_score_pct"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
