#!/usr/bin/env node

import {
  installKiroHook,
} from "./lib/codex-worklog-installer.mjs";

function printUsage() {
  console.log(`Usage:
  node install-kiro-worklog-hook.mjs --project <path>

Options:
  --project <path> Install into <path>/.kiro/hooks for one repository
  --help           Show this message`);
}

function parseArgs(argv) {
  let projectPath = null;

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--help" || arg === "-h") {
      return { help: true };
    }
    if (arg === "--project") {
      projectPath = argv[index + 1];
      index += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }

  if (!projectPath) {
    throw new Error("Missing required --project <path>.");
  }

  return {
    help: false,
    projectPath,
  };
}

async function main() {
  const parsed = parseArgs(process.argv.slice(2));
  if (parsed.help) {
    printUsage();
    return;
  }

  const result = await installKiroHook({
    targetRoot: parsed.projectPath,
  });

  console.log("Installed Kiro worklog hook");
  console.log(`Hooks directory: ${result.hooksRoot}`);
  console.log(`Hook file: ${result.hookPath}`);
  console.log("Restart Kiro if it does not pick up the new hook automatically.");
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
