import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  access,
  copyFile,
  mkdtemp,
  mkdir,
  readdir,
  readFile,
  writeFile,
} from "node:fs/promises";

import {
  HOOK_STATUS_MESSAGES,
  buildHookCommands,
  installWorklogHook,
  mergeHooksJsonText,
  upsertCodexHooksFeature,
} from "../lib/codex-worklog-installer.mjs";

const repoRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const templateRoot = path.join(repoRoot, "template");
const internalTitlePrompt = `You are a helpful assistant. You will be presented with a user prompt, and your job is to provide a short title for a task that will be created from that prompt.
The tasks typically have to do with coding-related tasks, for example requests for bug fixes or questions about a codebase. The title you generate will be shown in the UI to represent the prompt.
Generate a concise UI title (18-36 characters) for this task.
Return only the title. No quotes or trailing punctuation.`;

function runCommand(command, args, options = {}) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    ...options,
  });
  assert.equal(
    result.status,
    0,
    `${command} ${args.join(" ")} failed:\n${result.stderr || result.stdout}`,
  );
  return result;
}

async function exists(targetPath) {
  try {
    await access(targetPath);
    return true;
  } catch {
    return false;
  }
}

async function prepareHookRepo() {
  const tempRoot = await mkdtemp(
    path.join(os.tmpdir(), "codex-worklog-hook-runtime-"),
  );
  const worktreeRoot = path.join(tempRoot, "repo");
  const hookRoot = path.join(worktreeRoot, ".codex", "worklog-hook");
  await mkdir(worktreeRoot, { recursive: true });
  await mkdir(hookRoot, { recursive: true });
  runCommand("git", ["init", "-q"], { cwd: worktreeRoot });

  for (const filename of [
    "cache_user_prompt.py",
    "log_turn.py",
    "summary_schema.json",
  ]) {
    await copyFile(
      path.join(templateRoot, filename),
      path.join(hookRoot, filename),
    );
  }

  return {
    worktreeRoot,
    hookRoot,
  };
}

function runHookScript(scriptPath, payload, options = {}) {
  const result = runCommand(
    "/usr/bin/python3",
    [scriptPath],
    {
      input: JSON.stringify(payload),
      ...options,
    },
  );
  return JSON.parse(result.stdout || "{}");
}

async function findLogFile(worktreeRoot) {
  const logRoot = path.join(worktreeRoot, ".codex", "work-log");
  const entries = await readdir(logRoot);
  const rawLog = entries.find((entry) => entry.startsWith("raw-"));
  assert.ok(rawLog, "expected a raw work-log file to be created");
  return path.join(logRoot, rawLog);
}

test("upsertCodexHooksFeature appends features table when missing", () => {
  const updated = upsertCodexHooksFeature('model = "gpt-5.4"\n');

  assert.match(updated, /\[features\]\ncodex_hooks = true/);
});

test("upsertCodexHooksFeature updates an existing features table", () => {
  const updated = upsertCodexHooksFeature(
    '[features]\ncodex_hooks = false\nfast_mode = true\n',
  );

  assert.match(updated, /\[features\]\ncodex_hooks = true\nfast_mode = true/);
  assert.equal(updated.match(/codex_hooks = true/g)?.length, 1);
});

test("mergeHooksJsonText preserves existing hooks and installs managed entries idempotently", () => {
  const commands = buildHookCommands({
    scope: "project",
    targetRoot: "/tmp/example-repo",
  });
  const existing = JSON.stringify(
    {
      hooks: {
        Stop: [
          {
            matcher: ".*",
            hooks: [
              {
                type: "command",
                command: "echo existing-stop",
              },
            ],
          },
        ],
      },
    },
    null,
    2,
  );

  const merged = mergeHooksJsonText(existing, commands);
  const mergedAgain = mergeHooksJsonText(merged, commands);
  const parsed = JSON.parse(mergedAgain);

  assert.equal(parsed.hooks.Stop.length, 2);
  assert.equal(parsed.hooks.UserPromptSubmit.length, 1);

  const stopHooks = parsed.hooks.Stop.flatMap((entry) => entry.hooks ?? []);
  const promptHooks = parsed.hooks.UserPromptSubmit.flatMap(
    (entry) => entry.hooks ?? [],
  );

  assert.equal(
    stopHooks.filter(
      (hook) => hook.statusMessage === HOOK_STATUS_MESSAGES.stop,
    ).length,
    1,
  );
  assert.equal(
    promptHooks.filter(
      (hook) => hook.statusMessage === HOOK_STATUS_MESSAGES.userPromptSubmit,
    ).length,
    1,
  );
});

test("installWorklogHook installs project-scoped files and merges config", async () => {
  const tempRoot = await mkdtemp(
    path.join(os.tmpdir(), "codex-worklog-project-"),
  );
  const repoRoot = path.join(tempRoot, "repo");
  await mkdir(path.join(repoRoot, ".git"), { recursive: true });
  await mkdir(path.join(repoRoot, ".codex"), { recursive: true });
  await writeFile(
    path.join(repoRoot, ".codex", "config.toml"),
    'model = "gpt-5.4"\n',
    "utf8",
  );

  await installWorklogHook({
    scope: "project",
    targetRoot: repoRoot,
    force: false,
  });

  const config = await readFile(
    path.join(repoRoot, ".codex", "config.toml"),
    "utf8",
  );
  const hooks = JSON.parse(
    await readFile(path.join(repoRoot, ".codex", "hooks.json"), "utf8"),
  );
  const stopHooks = hooks.hooks.Stop.flatMap((entry) => entry.hooks ?? []);

  assert.match(config, /\[features\]\ncodex_hooks = true/);
  assert.ok(
    stopHooks.some(
      (hook) =>
        hook.command.includes("git rev-parse --show-toplevel") &&
        hook.statusMessage === HOOK_STATUS_MESSAGES.stop,
    ),
  );

  for (const relativePath of [
    "cache_user_prompt.py",
    "log_turn.py",
    "summary_schema.json",
    "manifest.json",
  ]) {
    const installed = path.join(
      repoRoot,
      ".codex",
      "worklog-hook",
      relativePath,
    );
    const content = await readFile(installed, "utf8");
    assert.ok(content.length > 0);
  }
});

test("installWorklogHook installs global-scoped files under the target .codex root", async () => {
  const tempRoot = await mkdtemp(
    path.join(os.tmpdir(), "codex-worklog-global-"),
  );
  const homeRoot = path.join(tempRoot, "home");
  const codexRoot = path.join(homeRoot, ".codex");
  await mkdir(codexRoot, { recursive: true });

  await installWorklogHook({
    scope: "global",
    targetRoot: codexRoot,
    force: false,
  });

  const hooks = JSON.parse(
    await readFile(path.join(codexRoot, "hooks.json"), "utf8"),
  );
  const promptHooks = hooks.hooks.UserPromptSubmit.flatMap(
    (entry) => entry.hooks ?? [],
  );

  assert.ok(
    promptHooks.some(
      (hook) =>
        hook.command.includes(path.join(codexRoot, "worklog-hook")) &&
        hook.statusMessage === HOOK_STATUS_MESSAGES.userPromptSubmit,
    ),
  );
});

test("cache_user_prompt ignores internal title-generation prompts", async () => {
  const { worktreeRoot, hookRoot } = await prepareHookRepo();

  runHookScript(
    path.join(hookRoot, "cache_user_prompt.py"),
    {
      session_id: "internal-session",
      turn_id: "turn-internal",
      cwd: worktreeRoot,
      prompt: internalTitlePrompt,
    },
    { cwd: worktreeRoot },
  );

  assert.equal(
    await exists(
      path.join(
        worktreeRoot,
        ".codex",
        "work-log",
        ".state",
        "session-internal-session.json",
      ),
    ),
    false,
  );
});

test("log_turn writes real user prompts and skips internal prompts", async () => {
  const { worktreeRoot, hookRoot } = await prepareHookRepo();
  const cacheScript = path.join(hookRoot, "cache_user_prompt.py");
  const logScript = path.join(hookRoot, "log_turn.py");

  runHookScript(
    cacheScript,
    {
      session_id: "user-session",
      turn_id: "turn-user",
      cwd: worktreeRoot,
      prompt: "帮我看下，日志有效果没",
    },
    { cwd: worktreeRoot },
  );

  await writeFile(path.join(worktreeRoot, "notes.txt"), "updated\n", "utf8");

  runHookScript(
    logScript,
    {
      session_id: "user-session",
      turn_id: "turn-user",
      cwd: worktreeRoot,
      last_assistant_message: "我已经核对了日志输出。",
    },
    {
      cwd: worktreeRoot,
      env: {
        ...process.env,
        CODEX_HOOK_DISABLE_AI: "1",
      },
    },
  );

  const logFile = await findLogFile(worktreeRoot);
  const userLog = await readFile(logFile, "utf8");
  assert.match(userLog, /帮我看下，日志有效果没/);
  assert.equal((userLog.match(/### \[/g) ?? []).length, 1);

  runHookScript(
    cacheScript,
    {
      session_id: "internal-session",
      turn_id: "turn-internal",
      cwd: worktreeRoot,
      prompt: internalTitlePrompt,
    },
    { cwd: worktreeRoot },
  );

  runHookScript(
    logScript,
    {
      session_id: "internal-session",
      turn_id: "turn-internal",
      cwd: worktreeRoot,
      last_assistant_message: "查看日志效果",
    },
    {
      cwd: worktreeRoot,
      env: {
        ...process.env,
        CODEX_HOOK_DISABLE_AI: "1",
      },
    },
  );

  const finalLog = await readFile(logFile, "utf8");
  assert.equal((finalLog.match(/### \[/g) ?? []).length, 1);
  assert.doesNotMatch(finalLog, /Generate a concise UI title/);
});
