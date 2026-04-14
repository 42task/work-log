#!/usr/bin/env node

import {
  installClaudeHook,
  resolveClaudeCliTarget,
} from "./lib/work-log-installer.mjs";

function printUsage() {
  console.log(`Usage:
  node install-claude-code-work-log.mjs --global [--force]
  node install-claude-code-work-log.mjs --project <path> [--force]

Options:
  --global         Install into ~/.claude for all Claude Code sessions on this machine
  --project <path> Install into <path>/.claude for one repository
  --force          Take over an existing unmanaged worklog-hook directory
  --help           Show this message`);
}

function parseArgs(argv) {
  let scope = null;
  let projectPath = null;
  let force = false;

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--help" || arg === "-h") {
      return { help: true };
    }
    if (arg === "--force") {
      force = true;
      continue;
    }
    if (arg === "--global") {
      scope = "global";
      continue;
    }
    if (arg === "--project") {
      scope = "project";
      projectPath = argv[index + 1];
      index += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }

  if (!scope) {
    throw new Error("Choose either --global or --project <path>.");
  }
  if (scope === "project" && !projectPath) {
    throw new Error("Missing path after --project.");
  }

  return {
    help: false,
    scope,
    projectPath,
    force,
  };
}

async function main() {
  const parsed = parseArgs(process.argv.slice(2));
  if (parsed.help) {
    printUsage();
    return;
  }

  const targetRoot = resolveClaudeCliTarget(parsed);
  const result = await installClaudeHook({
    scope: parsed.scope,
    targetRoot,
    force: parsed.force,
  });

  console.log(`Installed Work Log for Claude Code (${result.scope})`);
  console.log(`Managed files: ${result.managedRoot}`);
  console.log(`Settings file: ${result.settingsPath}`);
  console.log("Restart Claude Code to load the new hook configuration.");
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
