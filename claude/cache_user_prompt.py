#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def installed_claude_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def session_cwd(payload: dict) -> Path:
    return Path(payload.get("cwd") or os.getcwd()).resolve()


def git_root(cwd: Path) -> Optional[Path]:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return Path(output).resolve() if output else None


def project_root(payload: dict) -> Path:
    configured = os.environ.get("CLAUDE_PROJECT_DIR")
    if configured:
        return Path(configured).resolve()
    root = git_root(session_cwd(payload))
    return root if root is not None else session_cwd(payload)


def claude_dir(payload: dict) -> Path:
    override = os.environ.get("CLAUDE_WORKLOG_BASE_DIR")
    if override:
        return Path(override).resolve()

    root = project_root(payload)
    target = root / ".claude"
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except OSError:
        return installed_claude_dir()


def work_log_dir(payload: dict) -> Path:
    return claude_dir(payload) / "work-log"


def state_dir(payload: dict) -> Path:
    return work_log_dir(payload) / ".state"


def sanitize_text(text: str) -> str:
    sanitized = text or ""
    replacements = [
        (r"sk-[A-Za-z0-9_-]{8,}", "[已脱敏]"),
        (r"(?i)\bBearer\s+[A-Za-z0-9._\-]+\b", "Bearer [已脱敏]"),
        (
            r"(?i)\b(api[_\- ]?key|token|secret|password|passwd)\b\s*[:=]\s*\S+",
            lambda match: f"{match.group(1)}=[已脱敏]",
        ),
        (r"[\w.\-]+@[\w.\-]+\.\w+", "[已脱敏邮箱]"),
        (r"(?<!\d)(?:\+?\d[\d \-]{7,}\d)(?!\d)", "[已脱敏电话]"),
    ]
    for pattern, replacement in replacements:
        sanitized = re.sub(pattern, replacement, sanitized)
    return sanitized.strip()


def snapshot_git_status(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def truncate_for_notice(text: str, limit: int = 120) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


INTERNAL_PROMPT_MARKERS = (
    "your job is to provide a short title for a task that will be created from that prompt",
    "generate a concise ui title (18-36 characters) for this task",
    "generate a clear, informative task title based solely on the prompt provided",
    "你是一个工作日志摘要助手。请根据以下内容输出 json",
)


def is_internal_prompt(prompt: str) -> bool:
    compact = " ".join((prompt or "").split()).lower()
    return any(marker in compact for marker in INTERNAL_PROMPT_MARKERS)


def main() -> int:
    payload = json.load(sys.stdin)
    state_root = state_dir(payload)
    state_root.mkdir(parents=True, exist_ok=True)

    session_id = payload.get("session_id", "unknown")
    prompt = sanitize_text(payload.get("prompt", ""))
    if is_internal_prompt(prompt):
        print(json.dumps({"continue": True}, ensure_ascii=False))
        return 0

    snapshot_root = project_root(payload)
    snapshot = snapshot_git_status(snapshot_root)

    record = {
        "session_id": session_id,
        "prompt": prompt,
        "git_status": snapshot,
        "workspace_root": str(snapshot_root),
        "updated_at": datetime.now().astimezone().isoformat(),
    }

    target = state_root / f"session-{session_id}.json"
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    notice = truncate_for_notice(prompt or "空问题")
    print(
        json.dumps(
            {
                "continue": True,
                "systemMessage": f"问题：{notice}",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
