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


def project_root(payload: dict, cached: dict) -> Path:
    configured = os.environ.get("CLAUDE_PROJECT_DIR")
    if configured:
        return Path(configured).resolve()
    cached_root = cached.get("workspace_root")
    if cached_root:
        return Path(cached_root).resolve()
    root = git_root(session_cwd(payload))
    return root if root is not None else session_cwd(payload)


def claude_dir(payload: dict, cached: dict) -> Path:
    override = os.environ.get("CLAUDE_WORKLOG_BASE_DIR")
    if override:
        return Path(override).resolve()

    root = project_root(payload, cached)
    target = root / ".claude"
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except OSError:
        return installed_claude_dir()


def work_log_dir(payload: dict, cached: dict) -> Path:
    return claude_dir(payload, cached) / "work-log"


def state_dir(payload: dict, cached: dict) -> Path:
    return work_log_dir(payload, cached) / ".state"


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


def truncate_text(text: str, limit: int = 1200) -> str:
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


def load_state(payload: dict) -> dict:
    session_id = payload.get("session_id", "unknown")
    override = os.environ.get("CLAUDE_WORKLOG_BASE_DIR")
    roots: list[Path] = []
    if override:
        roots.append(Path(override).resolve())

    configured = os.environ.get("CLAUDE_PROJECT_DIR")
    if configured:
        roots.append(Path(configured).resolve() / ".claude")

    repo_root = git_root(session_cwd(payload))
    if repo_root is not None:
        roots.append(repo_root / ".claude")

    roots.append(installed_claude_dir())

    for root in roots:
        candidate = root / "work-log" / ".state" / f"session-{session_id}.json"
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    return {}


def save_state(payload: dict, cached: dict, updated: dict) -> None:
    state_root = state_dir(payload, cached)
    state_root.mkdir(parents=True, exist_ok=True)
    session_id = payload.get("session_id", "unknown")
    target = state_root / f"session-{session_id}.json"
    target.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")


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


def status_map(lines: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in lines:
        if len(line) < 4:
            continue
        mapping[line[3:]] = line[:2]
    return mapping


def changed_files_since_snapshot(before: list[str], after: list[str]) -> list[str]:
    previous = status_map(before)
    current = status_map(after)
    changed: list[str] = []
    for path, state in current.items():
        if previous.get(path) != state:
            changed.append(path)
    return sorted(changed)


def extract_paths_from_text(root: Path, text: str) -> list[str]:
    matches = re.findall(r"(?:`|^|\s)([A-Za-z0-9_./-]+\.[A-Za-z0-9_]+)(?:`|$|\s)", text or "")
    results: list[str] = []
    for raw in matches:
        if raw.startswith(("http://", "https://")):
            continue
        candidate = (root / raw).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.exists():
            rel = str(candidate.relative_to(root))
            if rel not in results:
                results.append(rel)
    return results


def fallback_summary(prompt: str, assistant_message: str, candidate_files: list[str]) -> dict:
    question = truncate_text(prompt or "本轮未识别到明确问题。", limit=90)
    solution = (
        "已根据本轮回答生成摘要并写入工作日志。"
        if assistant_message
        else "无实质性变更"
    )
    return {
        "question": question,
        "solution": truncate_text(solution, limit=160),
        "files": candidate_files[:5],
    }


def append_log(payload: dict, cached: dict, entry: dict) -> Path:
    now = datetime.now().astimezone()
    log_root = work_log_dir(payload, cached)
    log_root.mkdir(parents=True, exist_ok=True)
    target = log_root / f"raw-{now:%Y-%m-%d}.md"

    if not target.exists():
        target.write_text(f"# 工作日志 {now:%Y-%m-%d}\n", encoding="utf-8")

    file_label = "、".join(entry["files"]) if entry["files"] else "无"
    block = (
        "\n---\n"
        f"### [{now:%H:%M}]\n"
        f"**问题：** {entry['question']}\n"
        f"**解决：** {entry['solution']}\n"
        f"**涉及文件：** {file_label}\n"
        "---\n"
    )
    with target.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return target


def display_path(root: Path, payload: dict, cached: dict, target: Path) -> str:
    for base in (root, claude_dir(payload, cached)):
        try:
            return str(target.relative_to(base))
        except ValueError:
            continue
    return str(target)


def main() -> int:
    payload = json.load(sys.stdin)
    cached = load_state(payload)

    if payload.get("stop_hook_active"):
        print(json.dumps({"continue": True}, ensure_ascii=False))
        return 0

    root = project_root(payload, cached)
    prompt = sanitize_text(cached.get("prompt", ""))
    if not prompt or is_internal_prompt(prompt):
        print(json.dumps({"continue": True}, ensure_ascii=False))
        return 0

    assistant_message = sanitize_text(payload.get("last_assistant_message", ""))
    before = cached.get("git_status", [])
    after = snapshot_git_status(root)

    candidate_files = changed_files_since_snapshot(before, after)
    for path in extract_paths_from_text(root, assistant_message):
        if path not in candidate_files:
            candidate_files.append(path)

    entry = fallback_summary(prompt, assistant_message, candidate_files)
    target = append_log(payload, cached, entry)

    updated_state = dict(cached)
    updated_state["updated_at"] = datetime.now().astimezone().isoformat()
    save_state(payload, cached, updated_state)

    print(
        json.dumps(
            {
                "continue": True,
                "systemMessage": f"已写入 {display_path(root, payload, cached, target)}：{entry['question']}",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
