# Work Log

一个可复用的工作日志安装包，支持 Codex、Kiro 和 Claude Code。安装后会把每轮用户问题与处理摘要统一写入项目根目录下的 `./work-log/raw-YYYY-MM-DD.md`。

## Prerequisites

- 已安装 Node.js
- 使用 Codex 时，已安装 Codex CLI
- 使用 Claude Code 时，已安装 Claude Code
- 目标目录下可写 `.codex/`、`.kiro/`、`.claude/` 与项目根目录

## Install In This Repository

如果当前仓库已经把本项目作为子模块挂载在 `tools/work-log`，在项目根目录执行：

```bash
node tools/work-log/install-codex-work-log.mjs --project .
```

如果要给同一个项目安装 Kiro hook：

```bash
node tools/work-log/install-kiro-work-log.mjs --project .
```

如果要给同一个项目安装 Claude Code hook：

```bash
node tools/work-log/install-claude-code-work-log.mjs --project .
```

## Install From This Package Repository

如果你直接 clone 了这个仓库，在仓库根目录执行：

```bash
node install-codex-work-log.mjs --project /path/to/your/repo
```

全局安装到当前用户的 `~/.codex`：

```bash
node install-codex-work-log.mjs --global
```

如果目标目录里已经存在未受管的 `worklog-hook` 目录，可加 `--force` 接管：

```bash
node install-codex-work-log.mjs --project /path/to/your/repo --force
```

安装 Kiro hook 到某个项目：

```bash
node install-kiro-work-log.mjs --project /path/to/your/repo
```

安装 Claude Code hook 到某个项目：

```bash
node install-claude-code-work-log.mjs --project /path/to/your/repo
```

全局安装 Claude Code hook 到当前用户的 `~/.claude`：

```bash
node install-claude-code-work-log.mjs --global
```

## What Gets Installed

- `.codex/worklog-hook/` 下的受管脚本与 schema
- `.codex/config.toml` 中的 `features.codex_hooks = true`
- `.codex/hooks.json` 中的 `UserPromptSubmit` 与 `Stop` hook
- `.codex/work-log/.state/` 用于 Codex prompt / turn 缓存
- `.kiro/hooks/record-worklog.kiro.hook` Kiro hook 文件
- `.claude/worklog-hook/` 下的受管脚本与 schema
- `.claude/settings.json` 中的 `UserPromptSubmit` 与 `Stop` hook
- `.claude/work-log/.state/` 用于 Claude Code session 缓存
- `./work-log/raw-YYYY-MM-DD.md` 共享最终日志文件

## Verify

1. 确认 `.codex/config.toml` 已包含 `features.codex_hooks = true`
2. 确认 `.codex/hooks.json` 已包含 `UserPromptSubmit` 与 `Stop`
3. 确认 `.kiro/hooks/record-worklog.kiro.hook` 已存在
4. 确认 `.claude/settings.json` 已包含 `UserPromptSubmit` 与 `Stop`
5. 重启 Codex / Kiro / Claude Code
6. 发起一轮对话后，确认项目根目录下的 `./work-log/raw-YYYY-MM-DD.md` 已生成

## Git Ignore

如果不希望把日志提交到仓库，请自行决定是否将 `work-log/` 加入 `.gitignore`。安装器不会自动修改你的忽略规则。

## Notes For Agents

如果你是 Codex、Claude Code 或 Kiro 里的 agent，并且要为当前仓库自动安装 Work Log，优先执行：

```bash
node tools/work-log/install-codex-work-log.mjs --project .
```

如果用户还在使用 Kiro，再执行：

```bash
node tools/work-log/install-kiro-work-log.mjs --project .
```

如果用户还在使用 Claude Code，再执行：

```bash
node tools/work-log/install-claude-code-work-log.mjs --project .
```

安装完成后提醒用户重启对应客户端，使新 hook 配置生效。
