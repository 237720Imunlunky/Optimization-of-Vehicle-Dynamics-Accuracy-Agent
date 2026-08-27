"""检查GitHub发布内容，阻止私有数据、商业模型、本机路径和密钥进入版本库。"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
FORBIDDEN_SUFFIXES = {".blf", ".dbc", ".vsb", ".erd", ".dll", ".exe", ".par"}
REQUIRED_FILES = {
    "README.md", "LICENSE", "项目实现/部署包/install_agent.cmd",
    "项目实现/部署包/verify_installation.cmd",
    "项目实现/tools/convert_carsim_vsb.py",
    "项目实现/demo_assets/formal_acceptance.demo.json",
    "项目实现/config/runtime.example.json",
    "项目实现/Agent交互界面/config/llm_api.example.json",
}


def tracked_files() -> list[Path]:
    """读取Git索引中的真实路径，禁用中文路径转义。"""
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "-z"],
        cwd=ROOT, capture_output=True, check=True,
    )
    return [Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]


def forbidden_path(path: Path) -> str | None:
    """返回禁止发布原因；README形式的本机资产说明允许提交。"""
    normalized = path.as_posix()
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return f"禁止发布的文件类型：{path.suffix}"
    if normalized.startswith("实车数据/") or "/输出/" in f"/{normalized}/":
        return "实车数据或生成输出不得提交"
    if "/local_assets/" in f"/{normalized}/" and path.name != "README.md":
        return "local_assets只允许提交README"
    if path.name in {"runtime.local.json", "llm_api.local.json", "admission_reviews.local.json"}:
        return "本机或敏感配置不得提交"
    return None


def text_leaks(path: Path) -> list[str]:
    """扫描小型文本文件中的密钥和开发者绝对路径。"""
    absolute = ROOT / path
    if not absolute.is_file() or absolute.stat().st_size > 2 * 1024 * 1024:
        return []
    try:
        text = absolute.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError):
        return []
    leaks = []
    patterns = {
        "疑似API密钥令牌": r"sk-[A-Za-z0-9_-]{16,}",
        "疑似Bearer令牌": r"Bearer\s+[A-Za-z0-9._-]{20,}",
        "开发者用户目录": r"[A-Za-z]:\\Users\\[^\\\s]+",
        "开发者F盘中文绝对路径": r"F:\\桌面任务\\",
    }
    for label, pattern in patterns.items():
        if re.search(pattern, text, flags=re.IGNORECASE):
            leaks.append(label)
    return leaks


def main() -> None:
    """输出全部阻塞项并以非零状态阻止不安全发布。"""
    tracked = tracked_files()
    issues = []
    for path in tracked:
        reason = forbidden_path(path)
        if reason:
            issues.append(f"{path}: {reason}")
        issues.extend(f"{path}: {leak}" for leak in text_leaks(path))
    issues.extend(f"缺少公开发布文件：{name}" for name in sorted(
        name for name in REQUIRED_FILES if not (ROOT / name).is_file()
    ))
    if issues:
        print("公开发布检查失败：")
        print("\n".join(f"- {item}" for item in issues))
        raise SystemExit(1)
    print(f"公开发布检查通过：已检查{len(tracked)}个Git跟踪文件")


if __name__ == "__main__":
    main()
