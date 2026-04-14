import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  access,
  copyFile,
  mkdir,
  readFile,
  readdir,
  stat,
  writeFile,
} from "node:fs/promises";

const moduleDir = path.dirname(fileURLToPath(import.meta.url));
const templateRoot = path.resolve(moduleDir, "../template");
const kiroRoot = path.resolve(moduleDir, "../kiro");

export const INSTALLER_VERSION = "1.0.0";
export const MANAGED_DIRNAME = "worklog-hook";
export const MANAGED_MANIFEST = "manifest.json";
export const MANAGED_KIRO_HOOK_FILENAME = "record-worklog.kiro.hook";
export const HOOK_STATUS_MESSAGES = {
  userPromptSubmit: "Caching prompt for worklog",
  stop: "Writing worklog entry",
};

const TEMPLATE_FILES = [
  "cache_user_prompt.py",
  "log_turn.py",
  "summary_schema.json",
];

function normalizeNewlines(text) {
  return text.replace(/\r\n/g, "\n");
}

function topLevelTablePattern(name) {
  return new RegExp(`^\\s*\\[${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\]\\s*(?:#.*)?$`);
}

function anyTopLevelTablePattern() {
  return /^\s*\[[^\[\]]+\]\s*(?:#.*)?$/;
}

export function upsertCodexHooksFeature(input = "") {
  const normalized = normalizeNewlines(input);
  if (!normalized.trim()) {
    return "[features]\ncodex_hooks = true\n";
  }

  const lines = normalized.split("\n");
  const featuresPattern = topLevelTablePattern("features");
  const tablePattern = anyTopLevelTablePattern();

  let featuresStart = -1;
  for (let index = 0; index < lines.length; index += 1) {
    if (featuresPattern.test(lines[index])) {
      featuresStart = index;
      break;
    }
  }

  if (featuresStart === -1) {
    const trimmed = normalized.trimEnd();
    return `${trimmed}\n\n[features]\ncodex_hooks = true\n`;
  }

  let featuresEnd = lines.length;
  for (let index = featuresStart + 1; index < lines.length; index += 1) {
    if (tablePattern.test(lines[index])) {
      featuresEnd = index;
      break;
    }
  }

  let replaced = false;
  for (let index = featuresStart + 1; index < featuresEnd; index += 1) {
    if (/^\s*codex_hooks\s*=/.test(lines[index])) {
      lines[index] = "codex_hooks = true";
      replaced = true;
      break;
    }
  }

  if (!replaced) {
    lines.splice(featuresEnd, 0, "codex_hooks = true");
  }

  return `${lines.join("\n").trimEnd()}\n`;
}

export function buildHookCommands({ scope, targetRoot }) {
  if (scope === "project") {
    return {
      userPromptSubmit:
        '/usr/bin/python3 "$(git rev-parse --show-toplevel)/.codex/worklog-hook/cache_user_prompt.py"',
      stop: '/usr/bin/python3 "$(git rev-parse --show-toplevel)/.codex/worklog-hook/log_turn.py"',
    };
  }

  const installedRoot = path.join(targetRoot, MANAGED_DIRNAME);
  return {
    userPromptSubmit: `/usr/bin/python3 "${path.join(
      installedRoot,
      "cache_user_prompt.py",
    )}"`,
    stop: `/usr/bin/python3 "${path.join(installedRoot, "log_turn.py")}"`,
  };
}

function managedHookBasename(eventName) {
  return eventName === "Stop" ? "log_turn.py" : "cache_user_prompt.py";
}

function desiredHookForEvent(eventName, commands) {
  if (eventName === "Stop") {
    return {
      type: "command",
      command: commands.stop,
      statusMessage: HOOK_STATUS_MESSAGES.stop,
      timeout: 120,
    };
  }

  return {
    type: "command",
    command: commands.userPromptSubmit,
    statusMessage: HOOK_STATUS_MESSAGES.userPromptSubmit,
  };
}

function isManagedHook(eventName, hook) {
  if (!hook || typeof hook !== "object" || Array.isArray(hook)) {
    return false;
  }

  const expectedStatus =
    eventName === "Stop"
      ? HOOK_STATUS_MESSAGES.stop
      : HOOK_STATUS_MESSAGES.userPromptSubmit;
  if (hook.statusMessage === expectedStatus) {
    return true;
  }

  const command = String(hook.command ?? "");
  return command.includes(`/worklog-hook/${managedHookBasename(eventName)}`);
}

function upsertManagedHookEntry(entries, eventName, desiredHook) {
  const nextEntries = Array.isArray(entries)
    ? entries.map((entry) => {
        if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
          return entry;
        }

        if (!Array.isArray(entry.hooks)) {
          return entry;
        }

        let changed = false;
        const hooks = entry.hooks.map((hook) => {
          if (isManagedHook(eventName, hook)) {
            changed = true;
            return desiredHook;
          }
          return hook;
        });

        if (!changed) {
          return entry;
        }

        return {
          ...entry,
          hooks,
        };
      })
    : [];

  const alreadyPresent = nextEntries.some(
    (entry) =>
      entry &&
      typeof entry === "object" &&
      Array.isArray(entry.hooks) &&
      entry.hooks.some((hook) => isManagedHook(eventName, hook)),
  );

  if (!alreadyPresent) {
    nextEntries.push({
      hooks: [desiredHook],
    });
  }

  return nextEntries;
}

export function mergeHooksJsonText(existingText, commands) {
  let parsed = { hooks: {} };

  if (existingText && existingText.trim()) {
    try {
      parsed = JSON.parse(existingText);
    } catch (error) {
      throw new Error(`Invalid hooks.json: ${error.message}`);
    }
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    parsed = { hooks: {} };
  }
  if (!parsed.hooks || typeof parsed.hooks !== "object" || Array.isArray(parsed.hooks)) {
    parsed.hooks = {};
  }

  for (const eventName of ["UserPromptSubmit", "Stop"]) {
    parsed.hooks[eventName] = upsertManagedHookEntry(
      parsed.hooks[eventName],
      eventName,
      desiredHookForEvent(eventName, commands),
    );
  }

  return `${JSON.stringify(parsed, null, 2)}\n`;
}

function resolveCodexRoot(scope, targetRoot) {
  const resolved = path.resolve(targetRoot);
  if (scope === "global") {
    return resolved;
  }
  return path.join(resolved, ".codex");
}

async function pathExists(targetPath) {
  try {
    await access(targetPath);
    return true;
  } catch {
    return false;
  }
}

async function readTextIfExists(targetPath) {
  if (!(await pathExists(targetPath))) {
    return "";
  }
  return readFile(targetPath, "utf8");
}

function resolveKiroHooksRoot(targetRoot) {
  return path.join(path.resolve(targetRoot), ".kiro", "hooks");
}

async function ensureManagedDirectory(managedRoot, force) {
  await mkdir(managedRoot, { recursive: true });
  const manifestPath = path.join(managedRoot, MANAGED_MANIFEST);
  if (await pathExists(manifestPath)) {
    return;
  }

  const contents = await readdir(managedRoot);
  if (contents.length === 0 || force) {
    return;
  }

  throw new Error(
    `Refusing to overwrite unmanaged directory: ${managedRoot}. Re-run with --force if you want to take it over.`,
  );
}

async function writeManagedTemplateFiles(managedRoot) {
  await mkdir(managedRoot, { recursive: true });

  for (const filename of TEMPLATE_FILES) {
    await copyFile(
      path.join(templateRoot, filename),
      path.join(managedRoot, filename),
    );
  }
}

async function writeManifest(managedRoot, scope) {
  const manifest = {
    name: "codex-worklog-hook",
    version: INSTALLER_VERSION,
    scope,
    installedAt: new Date().toISOString(),
  };
  await writeFile(
    path.join(managedRoot, MANAGED_MANIFEST),
    `${JSON.stringify(manifest, null, 2)}\n`,
    "utf8",
  );
}

export async function installWorklogHook({ scope, targetRoot, force = false }) {
  if (!["global", "project"].includes(scope)) {
    throw new Error(`Unsupported scope: ${scope}`);
  }

  const resolvedTarget = path.resolve(targetRoot);
  const codexRoot = resolveCodexRoot(scope, resolvedTarget);
  const managedRoot = path.join(codexRoot, MANAGED_DIRNAME);
  const commands = buildHookCommands({ scope, targetRoot: codexRoot });

  if (scope === "project") {
    const stats = await stat(resolvedTarget).catch(() => null);
    if (!stats || !stats.isDirectory()) {
      throw new Error(`Project path does not exist: ${resolvedTarget}`);
    }
  }

  await mkdir(codexRoot, { recursive: true });
  await ensureManagedDirectory(managedRoot, force);
  await writeManagedTemplateFiles(managedRoot);
  await writeManifest(managedRoot, scope);

  const configPath = path.join(codexRoot, "config.toml");
  const hooksPath = path.join(codexRoot, "hooks.json");

  const existingConfig = await readTextIfExists(configPath);
  const existingHooks = await readTextIfExists(hooksPath);

  await writeFile(
    configPath,
    upsertCodexHooksFeature(existingConfig),
    "utf8",
  );
  await writeFile(
    hooksPath,
    mergeHooksJsonText(existingHooks, commands),
    "utf8",
  );

  return {
    scope,
    targetRoot: resolvedTarget,
    codexRoot,
    managedRoot,
    configPath,
    hooksPath,
  };
}

export async function installKiroHook({ targetRoot }) {
  const resolvedTarget = path.resolve(targetRoot);
  const stats = await stat(resolvedTarget).catch(() => null);
  if (!stats || !stats.isDirectory()) {
    throw new Error(`Project path does not exist: ${resolvedTarget}`);
  }

  const hooksRoot = resolveKiroHooksRoot(resolvedTarget);
  await mkdir(hooksRoot, { recursive: true });

  const hookPath = path.join(hooksRoot, MANAGED_KIRO_HOOK_FILENAME);
  await copyFile(
    path.join(kiroRoot, MANAGED_KIRO_HOOK_FILENAME),
    hookPath,
  );

  return {
    targetRoot: resolvedTarget,
    hooksRoot,
    hookPath,
  };
}

export function resolveCliTarget({ scope, projectPath }) {
  if (scope === "global") {
    return path.join(os.homedir(), ".codex");
  }
  if (!projectPath) {
    throw new Error("Project path is required when using --project.");
  }
  return path.resolve(projectPath);
}
