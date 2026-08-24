# 东合施工队 (Donghe Construction Team)

[English](README.en.md) | **中文**

一套面向 AI coding agent 的**工程纪律 skill**。v0.6.1 起 SKILL.md 是一页路由器，核心仍只有三件事：

- **机器证据**：说"完成"必须有真实运行时证据（`scripts/collect_evidence.py` 生成），"build 通过"不算功能完成。
- **范围纪律**：动手前声明改动范围，只写声明范围内的文件，不顺手改别的。
- **诚实汇报**：禁用"搞定 / 没问题 / 应该可以 / 暂时 / 先这样"等空头词；盲区和未验证项明说。

任务按 S/M/L 分级：S/M 级 agent 自己干、零流程开销；只有 L 级（跨层 / 动 schema / 涉钱权安 / 真正需要并行）才拆任务卡、派便宜档工人并行施工。

当前版本见 `SKILL.md` 标题与 `CHANGELOG.md` 最新条目。

## 目录结构

| 路径 | 内容 |
|---|---|
| `SKILL.md` | 章程 + 按需加载路由 |
| `references/models.md` | Task 模型档位（按宿主列表，禁止 inherit） |
| `references/unattended.md` | 无守护模式细则 |
| `templates/任务卡模板.md` | L 级派工用的瘦身任务卡（≤ 40 行） |
| `agents/openai.yaml` | Codex agent 入口 |
| `scripts/collect_evidence.py` | 机器证据采集（命令失败则非 0） |
| `scripts/sync.sh` | 仓库 → 本地安装位置的单向同步 |
| `evals/evals.json` | 作者对照用例（不同步到安装位置） |
| `CHANGELOG.md` | 版本演化史 |

## 安装

本仓库是**唯一正本（SSOT）**。克隆后同步到 agent 的 skill 目录：

```bash
git clone https://github.com/donhauser001/donghe-construction-team.git
cd donghe-construction-team
scripts/sync.sh
```

`sync.sh` 默认同步到：

- Codex：`~/.codex/skills/donghe-construction-team/`
- Cursor：`~/.cursor/skills/donghe-construction-team/`（个人 skill；不要写进 `~/.cursor/skills-cursor/`）

如需其它位置，编辑脚本内的 `TARGETS` 数组。

## 修改流程

1. 只在本仓库内修改，**禁止直接改安装位置的文件**。
2. 版本号同步更新四处：`SKILL.md` frontmatter description（如涉及）、`SKILL.md` 标题与元信息、`agents/openai.yaml`、`CHANGELOG.md` 新条目。
3. 保持一页原则：新增规则先想清楚能不能删一条旧的；SKILL.md 超过约 120 行视为膨胀信号。
4. commit 后运行 `scripts/sync.sh` 下发到本地安装位置。

## License

[MIT](LICENSE)
