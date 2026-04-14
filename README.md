# Codex Worklog Hook

一个可复用的 Codex 工作日志 Hook 安装包。安装后会把每轮用户问题与处理摘要写入 `.codex/work-log/raw-YYYY-MM-DD.md`。

## Prerequisites

- 已安装 Codex CLI
- 已安装 Node.js
- 目标目录下可写 `.codex/`

## Install In This Repository

如果当前仓库已经把本项目作为子模块挂载在 `tools/codex-worklog-hook`，在项目根目录执行：

```bash
node tools/codex-worklog-hook/install-codex-worklog-hook.mjs --project .
```

## Install From This Package Repository

如果你直接 clone 了这个仓库，在仓库根目录执行：

```bash
node install-codex-worklog-hook.mjs --project /path/to/your/repo
```

全局安装到当前用户的 `~/.codex`：

```bash
node install-codex-worklog-hook.mjs --global
```

如果目标目录里已经存在未受管的 `worklog-hook` 目录，可加 `--force` 接管：

```bash
node install-codex-worklog-hook.mjs --project /path/to/your/repo --force
```

## What Gets Installed

- `.codex/worklog-hook/` 下的受管脚本与 schema
- `.codex/config.toml` 中的 `features.codex_hooks = true`
- `.codex/hooks.json` 中的 `UserPromptSubmit` 与 `Stop` hook
- `.codex/work-log/raw-YYYY-MM-DD.md` 日志文件

## Verify

1. 确认 `.codex/config.toml` 已包含 `features.codex_hooks = true`
2. 确认 `.codex/hooks.json` 已包含 `UserPromptSubmit` 与 `Stop`
3. 重启 Codex
4. 发起一轮对话后，确认 `.codex/work-log/raw-YYYY-MM-DD.md` 已生成

## Notes For Codex

如果你是 Codex，并且要为当前仓库自动安装 worklog hook，优先执行：

```bash
node tools/codex-worklog-hook/install-codex-worklog-hook.mjs --project .
```

安装完成后提醒用户重启 Codex，使新 hook 配置生效。
