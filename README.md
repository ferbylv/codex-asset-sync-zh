# codex-asset-sync-zh

安全地在本地 Codex 根目录和 Git 远程仓库之间，同步指定的 Codex 资产：`AGENTS.md`、`rules/` 与 `skills/`。

## 安全模型

计划模式是只读的。任何会改变状态的模式都需要 `--yes`；调用方必须先确认本地根目录、远程地址、分支、同步方向以及每项选定资产。恢复或应用会在替换前备份现有选定本地资产；首次恢复时若本地资产不存在，则没有可创建的备份。回滚会保护当前请求的资产，然后仅恢复既被请求、又列在最近一次完整备份清单中的资产。

Git 或校验失败是停止条件，不应临时编造恢复方案。变更模式会为每个根目录使用 `.meta-sync-lock` 串行化；如果进程中断遗留锁文件，只有在确认没有变更操作正在运行后，才能移除该精确锁文件。

## 要求与安装

- Python 3.9 或更高版本。
- 远程操作需要 `PATH` 中存在 Git。

将本目录复制到你的 agent 可加载的 skill 位置。命令可从本仓库运行，也可使用已安装 skill 的绝对目录；不要依赖相对的 `./skills/...` 脚本路径。

## CLI

先查看完整接口：

```bash
python3 scripts/sync.py --help
```

为某个本地根目录生成计划（默认模式也是只读）：

```bash
python3 scripts/sync.py --root /path/to/codex --plan
```

在获得明确授权后，从远程恢复或应用指定资产：

```bash
python3 scripts/sync.py --restore --yes --root /path/to/codex \
  --remote https://example.com/account/codex-assets.git --branch main \
  --asset AGENTS.md --asset rules --asset skills
python3 scripts/sync.py --apply --yes --root /path/to/codex \
  --remote https://example.com/account/codex-assets.git --branch main \
  --asset AGENTS.md
```

推送指定本地资产，或从最近的备份回滚指定资产：

```bash
python3 scripts/sync.py --push --yes --root /path/to/codex \
  --remote https://example.com/account/codex-assets.git --branch main \
  --asset AGENTS.md --asset rules --message "更新 Codex 资产"
python3 scripts/sync.py --rollback --yes --root /path/to/codex --asset AGENTS.md
```

## 模式语义

| 模式 | 效果 |
| --- | --- |
| 默认 / `--plan` | 只读计划；不会执行 Git 命令或写入本地文件。 |
| `--restore`、`--apply` | 用远程资产替换选定本地资产，并先备份现有选定本地资产。 |
| `--push` | 将选定本地资产写入临时远程克隆，再执行普通 Git 提交和推送。 |
| `--rollback` | 保护现有请求资产，然后只恢复最近完整备份清单中列出的请求资产。 |

## 测试与发布检查

```bash
python3 -m unittest discover -s tests -v
python3 scripts/sync.py --help
```

发布前，请确认本文档中的命令与 `--help` 输出的语义一致，并确认发布包包含 `SKILL.md`、本 README、`LICENSE` 和 `.gitignore`。

## 敏感信息与非目标

远程 URL、命令输出和 Git 配置可能包含凭据或私有元数据。请使用不含凭据的远程 URL，不要把秘密粘贴进命令或提交说明；一旦发现敏感信息，应停止操作。

本项目不会安装 Git 或 `gh`、登录、创建仓库、自动解决冲突、猜测分支或仓库名、强制推送、reset、merge、rebase，也不会同步整个仓库。

为保证可移植性，选定资产本身不能是符号链接。选定目录中嵌套的符号链接必须是相对链接，并且必须解析到同一选定目录树之内。
