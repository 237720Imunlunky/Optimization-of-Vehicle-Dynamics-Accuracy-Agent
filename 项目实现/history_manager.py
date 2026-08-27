"""任务级历史归档、空间统计、清理预览和受保护清理。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from config_loader import load_project_config
from data_admission import latest_admission_manifest
from runtime_paths import load_runtime_paths


TASK_PATTERN = re.compile(r"^(ui_\d{8}_\d{6})(?:_|$)")
ARCHIVE_FOLDER = "任务档案"


def read_json(path: Path, default: Any = None) -> Any:
    """读取JSON，缺失时返回默认值。"""
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def canonical_checksum(payload: dict[str, Any]) -> str:
    """计算不含自校验字段的稳定SHA256。"""
    content = {key: value for key, value in payload.items() if key != "summary_sha256"}
    serialized = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def directory_size(path: Path) -> int:
    """统计目录内普通文件总字节数，跳过读取失败文件。"""
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def task_id_from_name(name: str) -> str | None:
    """从现有零散候选目录名提取一次完整优化的任务编号。"""
    match = TASK_PATTERN.match(name)
    return match.group(1) if match else None


def task_directories(output_root: Path) -> dict[str, list[Path]]:
    """把同一时间戳生成的候选、评价和轮次状态归到一个逻辑任务。"""
    grouped: dict[str, list[Path]] = {}
    if not output_root.exists():
        return grouped
    for path in output_root.iterdir():
        if not path.is_dir() or path.name == ARCHIVE_FOLDER:
            continue
        task_id = task_id_from_name(path.name)
        if task_id:
            grouped.setdefault(task_id, []).append(path)
    return grouped


def latest_task_state(paths: list[Path]) -> tuple[Path | None, dict[str, Any] | None]:
    """读取任务中最后生成的状态文件。"""
    states = [folder / "agent_state.json" for folder in paths if (folder / "agent_state.json").exists()]
    if not states:
        return None, None
    latest = max(states, key=lambda path: path.stat().st_mtime)
    return latest, read_json(latest)


def copy_key_evidence(task_id: str, paths: list[Path], archive: Path) -> list[str]:
    """复制小体积关键判定证据，清理原始仿真后仍能审计接受与回退。"""
    evidence = archive / "关键证据"
    evidence.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    names = ("acceptance_decision.json", "round_experience.json", "candidate_parameters.json", "run_manifest.json")
    for folder in paths:
        for name in names:
            source = folder / name
            if not source.exists():
                continue
            destination = evidence / f"{folder.name}__{name}"
            shutil.copy2(source, destination)
            copied.append(str(destination))
    return copied


def finalize_task(task_id: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    """任务结束后提炼永久摘要并自校验；不执行任何清理。"""
    output_root = load_runtime_paths()["output_root"] / "LLM参数优化Agent"
    paths = task_directories(output_root).get(task_id, [])
    if not paths:
        raise FileNotFoundError(f"找不到任务输出：{task_id}")
    state_path, state = latest_task_state(paths)
    archive = output_root / ARCHIVE_FOLDER / task_id
    archive.mkdir(parents=True, exist_ok=True)
    copied = copy_key_evidence(task_id, paths, archive)
    admission_path = latest_admission_manifest()
    admission = read_json(admission_path, {}) if admission_path else {}
    experience_rounds = state.get("optimization_memory", {}).get("rounds", []) if state else []
    payload: dict[str, Any] = {
        "version": "1.0", "task_id": task_id, "finalized_at": datetime.now().isoformat(timespec="seconds"),
        "status": "completed", "source_directories": [str(path) for path in sorted(paths)],
        "source_size_bytes": sum(directory_size(path) for path in paths),
        "result": result or {}, "latest_state_path": str(state_path) if state_path else None,
        "best": state.get("best") if state else None,
        "evaluation_config_fingerprint": state.get("evaluation_config_fingerprint") if state else None,
        "data_fingerprint": admission.get("data_fingerprint"),
        "experience_summary": experience_rounds,
        "experience_extracted": bool(experience_rounds),
        "key_evidence": copied,
    }
    payload["summary_sha256"] = canonical_checksum(payload)
    summary_path = archive / "task_summary.json"
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (archive / "README.md").write_text(
        f"# 优化任务永久档案 {task_id}\n\n"
        "本目录永久保留任务摘要、最佳参数、配置/数据指纹、压缩经验和关键判定证据。"
        "只有摘要校验通过后，历史管理器才可能清理对应的大体积CarSim原始文件。\n",
        encoding="utf-8",
    )
    return payload


def verify_summary(summary: dict[str, Any] | None) -> bool:
    """验证摘要存在、经验已提炼且SHA256一致。"""
    return bool(
        summary and summary.get("experience_extracted")
        and summary.get("summary_sha256") == canonical_checksum(summary)
    )


def latest_active_task(grouped: dict[str, list[Path]]) -> str | None:
    """把含最新状态文件的任务视为当前最优续跑来源并永久保护。"""
    candidates: list[tuple[float, str]] = []
    for task_id, paths in grouped.items():
        state_path, _ = latest_task_state(paths)
        if state_path:
            candidates.append((state_path.stat().st_mtime, task_id))
    return max(candidates)[1] if candidates else None


def history_overview() -> dict[str, Any]:
    """返回任务空间、摘要状态、保护原因和可释放空间。"""
    config = load_project_config()["history_retention"]
    output_root = load_runtime_paths()["output_root"] / "LLM参数优化Agent"
    grouped = task_directories(output_root)
    ordered = sorted(grouped, key=lambda task_id: max(path.stat().st_mtime for path in grouped[task_id]), reverse=True)
    recent = set(ordered[: int(config["full_task_count"])])
    current = latest_active_task(grouped)
    tasks = []
    for task_id in ordered:
        archive = output_root / ARCHIVE_FOLDER / task_id
        summary = read_json(archive / "task_summary.json")
        reasons = []
        if task_id in recent:
            reasons.append(f"最近{int(config['full_task_count'])}次完整任务")
        if task_id == current:
            reasons.append("当前续跑/最优状态来源")
        if not verify_summary(summary):
            reasons.append("尚无已验证经验摘要")
        size = sum(directory_size(path) for path in grouped[task_id])
        eligible = not reasons
        tasks.append({
            "task_id": task_id, "size_bytes": size, "size_mb": round(size / 1024 / 1024, 2),
            "directory_count": len(grouped[task_id]), "summary_verified": verify_summary(summary),
            "experience_extracted": bool(summary and summary.get("experience_extracted")),
            "protected": bool(reasons), "protection_reasons": reasons, "cleanup_eligible": eligible,
            "estimated_reclaim_bytes": size if eligible else 0,
        })
    return {
        "automatic_cleanup_enabled": bool(config.get("automatic_cleanup_enabled", False)),
        "retained_full_task_count": int(config["full_task_count"]), "current_task_id": current,
        "total_size_bytes": sum(item["size_bytes"] for item in tasks),
        "estimated_reclaim_bytes": sum(item["estimated_reclaim_bytes"] for item in tasks),
        "tasks": tasks,
    }


def cleanup_eligible_tasks(confirm: str, task_ids: list[str] | None = None) -> dict[str, Any]:
    """仅在显式确认后清理预览中合格的任务目录，永久档案不受影响。"""
    if confirm != "DELETE_VERIFIED_HISTORY":
        raise ValueError("清理确认文字不正确，未删除任何文件")
    overview = history_overview()
    allowed = {item["task_id"] for item in overview["tasks"] if item["cleanup_eligible"]}
    requested = set(task_ids or allowed)
    blocked = sorted(requested - allowed)
    if blocked:
        raise ValueError(f"以下任务受保护或摘要未验证，禁止清理：{', '.join(blocked)}")
    output_root = load_runtime_paths()["output_root"] / "LLM参数优化Agent"
    grouped = task_directories(output_root)
    removed = []
    reclaimed = 0
    for task_id in sorted(requested):
        for path in grouped.get(task_id, []):
            resolved = path.resolve()
            if resolved.parent != output_root.resolve() or task_id_from_name(resolved.name) != task_id:
                raise RuntimeError(f"清理目标越界，已停止：{resolved}")
            reclaimed += directory_size(resolved)
            shutil.rmtree(resolved)
        removed.append(task_id)
    return {"removed_task_ids": removed, "reclaimed_bytes": reclaimed, "recoverable": False}
