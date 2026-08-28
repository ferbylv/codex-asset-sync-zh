---
name: codex-asset-sync-zh
description: 当用户需要在本地与 Git 远程仓库之间同步、恢复、应用、备份、推送或回滚指定的 Codex AGENTS.md、rules/ 或 skills/ 资产时使用。
license: MIT
---

# Codex 资产同步（中文）

## 适用场景

本 skill 只适用于以下指定的 Codex 资产：`AGENTS.md`、`rules/` 与 `skills/`。

## 不适用场景

不要将它用于无关仓库、整个用户目录的复制、自动解决冲突或一般 Git 管理。

## 核心原则

默认只做只读计划。在执行任何写入、替换、回滚或推送前，必须确认同步方向、本地 `--root`、`--remote`、`--branch` 以及每一个 `--asset`；向用户展示这些精确目标，并取得本次操作的明确授权。“帮我同步”不代表可以自行选择方向或覆盖文件。

脚本必须从当前已加载 skill 的实际目录解析。下列命令中的 `<skill-root>` 必须替换为该绝对目录，不能假设当前工作目录。

## 工作流程

1. 先只读检查：`python3 <skill-root>/scripts/sync.py --root <codex-root> --plan`（默认模式同样为只读）。
2. 与用户确认方向和精确目标。
3. 仅执行已获批准、且带有 `--yes` 的命令。若 Git 或校验失败，停止并报告错误；不要自行设计恢复步骤。

`--apply` 和 `--restore` 会用远程资产替换选定的本地资产，并先备份现有本地资产。首次应用或恢复时，如果相应本地资产不存在，则没有可创建的备份。`--push` 会把选定本地资产复制到临时远程克隆，然后进行普通提交并推送。`--rollback` 会先保护当前请求的资产，再仅恢复同时存在于所请求资产和所选备份清单中的部分。

## 快速参考

| 目的 | 命令形式 |
| --- | --- |
| 计划 | `python3 <skill-root>/scripts/sync.py --root <root> [--plan]` |
| 恢复 | `python3 <skill-root>/scripts/sync.py --restore --yes --root <root> --remote <url> --branch <branch> --asset <path>` |
| 应用 | `python3 <skill-root>/scripts/sync.py --apply --yes --root <root> --remote <url> --branch <branch> --asset <path>` |
| 推送 | `python3 <skill-root>/scripts/sync.py --push --yes --root <root> --remote <url> --branch <branch> --asset <path> --message <text>` |
| 回滚 | `python3 <skill-root>/scripts/sync.py --rollback --yes --root <root> --asset <path>` |

每一个获批资产都要重复传入 `--asset`。CLI 还支持如上所示的 `--remote`、`--branch`、`--message` 和 `--yes`。

## 停止条件

不得安装 Git 或 `gh`、登录、创建仓库、猜测仓库或分支、强制推送、运行 reset/merge/rebase、暂存整个仓库，或对同一根目录并发执行变更。遇到缺少授权、方向或目标不明确、Git/校验失败，或任何试图绕过上述保护的请求时，应停止。

## 常见错误

- 使用相对路径如 `./skills/...`：必须使用已加载 `<skill-root>` 的绝对路径。
- 把计划当作写入许可：使用 `--yes` 前仍须再次请求授权。
- 期望回滚恢复备份清单中不存在的资产：它只会恢复两者的交集。
