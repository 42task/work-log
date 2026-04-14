#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional


def installed_codex_dir() -> Path:
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


def workspace_root(payload: dict, cached: dict) -> Path:
    cached_root = cached.get("workspace_root")
    if cached_root:
        return Path(cached_root).resolve()
    root = git_root(session_cwd(payload))
    return root if root is not None else session_cwd(payload)


def codex_dir(payload: dict, cached: dict) -> Path:
    override = os.environ.get("CODEX_HOOK_BASE_DIR")
    if override:
        return Path(override).resolve()

    root = workspace_root(payload, cached)
    if (root / ".git").exists():
        target = root / ".codex"
        try:
            target.mkdir(parents=True, exist_ok=True)
            return target
        except OSError:
            return installed_codex_dir()
    return installed_codex_dir()


def shared_log_dir(payload: dict, cached: dict) -> Path:
    root = workspace_root(payload, cached)
    if (root / ".git").exists():
        return root / "work-log"
    return codex_dir(payload, cached) / "work-log"


def state_dir(payload: dict, cached: dict) -> Path:
    return codex_dir(payload, cached) / "work-log" / ".state"


def summary_schema_path() -> Path:
    return Path(__file__).resolve().with_name("summary_schema.json")


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
    override = os.environ.get("CODEX_HOOK_BASE_DIR")
    roots: list[Path] = []
    if override:
        roots.append(Path(override).resolve())

    repo_root = git_root(session_cwd(payload))
    if repo_root is not None:
        roots.append(repo_root / ".codex")

    roots.append(installed_codex_dir())

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


def shutil_which(binary: str) -> Optional[str]:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def summarize_with_codex(
    root: Path, prompt: str, assistant_message: str, candidate_files: list[str]
) -> Optional[dict]:
    if os.environ.get("CODEX_HOOK_DISABLE_AI") == "1":
        return None

    codex_binary = shutil_which("codex")
    if not codex_binary:
        return None

    candidate_block = "\n".join(f"- {path}" for path in candidate_files) or "- 无"
    prompt_text = f"""
你是一个工作日志摘要助手。请根据以下内容输出 JSON，且必须满足给定 schema。

要求：
1. 使用简体中文。
2. `question` 用一句话概括用户的本次问题或需求。
3. `solution` 用 2-3 句话概括本轮已经完成的处理；如果本轮只是闲聊或没有实质性工作，写“无实质性变更”。
4. `files` 只能从候选文件中挑选真正相关的关键文件；如果没有则输出空数组。
5. 严禁输出敏感信息。任何 API Key、密码、Token、Secret、邮箱、手机号、地址、姓名等都要替换成 `[已脱敏]`。
6. 不要输出 markdown，不要输出代码块，只输出 JSON。

用户问题：
{truncate_text(prompt or "无", limit=600)}

AI 最终回答：
{truncate_text(assistant_message or "无", limit=2400)}

候选文件：
{candidate_block}
""".strip()

    with tempfile.TemporaryDirectory(prefix="codex-hook-summary-") as tmpdir:
        output_file = Path(tmpdir) / "summary.json"
        command = [
            codex_binary,
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "-C",
            str(root),
            "-c",
            "features.codex_hooks=false",
            "-c",
            "features.plugins=false",
            "-c",
            'model_reasoning_effort="low"',
            "--output-schema",
            str(summary_schema_path()),
            "-o",
            str(output_file),
            prompt_text,
        ]
        result = subprocess.run(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if result.returncode != 0 or not output_file.exists():
            return None
        try:
            summary = json.loads(output_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    return summary if isinstance(summary, dict) else None


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


def normalize_summary(summary: dict, candidate_files: list[str]) -> dict:
    question = sanitize_text(str(summary.get("question", "")).strip()) or "本轮未识别到明确问题。"
    solution = sanitize_text(str(summary.get("solution", "")).strip()) or "无实质性变更"
    raw_files = summary.get("files", [])
    selected: list[str] = []
    if isinstance(raw_files, list):
        allowed = set(candidate_files)
        for item in raw_files:
            path = str(item).strip()
            if path in allowed and path not in selected:
                selected.append(path)
    return {
        "question": truncate_text(question, limit=90),
        "solution": truncate_text(solution, limit=160),
        "files": selected[:5],
    }


def append_log(payload: dict, cached: dict, entry: dict) -> Path:
    now = datetime.now().astimezone()
    log_root = shared_log_dir(payload, cached)
    log_root.mkdir(parents=True, exist_ok=True)
    target = log_root / f"raw-{now:%Y-%m-%d}.md"

    if not target.exists():
        target.write_text(f"# 工作日志 {now:%Y-%m-%d}\n", encoding="utf-8")

    file_label = "、".join(entry["files"]) if entry["files"] else "无"
    block = (
        "\n---\n"
        f"### [{now:%H:%M}]\n"
        f"**来源：** Codex\n"
        f"**问题：** {entry['question']}\n"
        f"**解决：** {entry['solution']}\n"
        f"**涉及文件：** {file_label}\n"
        "---\n"
    )
    with target.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return target


def display_path(root: Path, payload: dict, cached: dict, target: Path) -> str:
    for base in (root, codex_dir(payload, cached)):
        try:
            return str(target.relative_to(base))
        except ValueError:
            continue
    return str(target)


def main() -> int:
    payload = json.load(sys.stdin)
    cached = load_state(payload)
    turn_id = payload.get("turn_id")
    if cached.get("logged_turn_id") and cached.get("logged_turn_id") == turn_id:
        print(json.dumps({"continue": True}, ensure_ascii=False))
        return 0

    root = workspace_root(payload, cached)
    prompt = sanitize_text(cached.get("prompt", ""))
    cached_turn_id = cached.get("turn_id")
    if not prompt or is_internal_prompt(prompt) or (turn_id and cached_turn_id != turn_id):
        print(json.dumps({"continue": True}, ensure_ascii=False))
        return 0
    assistant_message = sanitize_text(payload.get("last_assistant_message", ""))
    before = cached.get("git_status", [])
    after = snapshot_git_status(root)

    candidate_files = changed_files_since_snapshot(before, after)
    for path in extract_paths_from_text(root, assistant_message):
        if path not in candidate_files:
            candidate_files.append(path)

    summary = summarize_with_codex(root, prompt, assistant_message, candidate_files)
    entry = normalize_summary(summary, candidate_files) if summary else fallback_summary(
        prompt, assistant_message, candidate_files
    )
    target = append_log(payload, cached, entry)

    updated_state = dict(cached)
    updated_state["logged_turn_id"] = turn_id
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
