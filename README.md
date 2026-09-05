# 东合施工队 (Donghe Construction Team)

[English](README.en.md) | **中文**

一套面向 AI coding agent 的**工程纪律 skill**。v0.6.1 起 SKILL.md 是一页路由器，核心仍只有三件事：

- **机器证据**：说"完成"必须有真实运行时证据（`scripts/collect_evidence.py` 生成），"build 通过"不算功能完成。
- **范围纪律**：动手前声明改动范围，只写声明范围内的文件，不顺手改别的。
- **诚实汇报**：禁用"搞定 / 没问题 / 应该可以 / 暂时 / 先这样"等空头词；盲区和未验证项明说。

任务按 S/M/L 分级：S/M 级 agent 自己干、零流程开销；只有 L 级（跨层 / 动 schema / 涉钱权安 / 真正需要并行）才拆任务卡、派便宜档工人并行施工。

当前版本见 `SKILL.md` 标题与 `CHANGELOG.md` 最新条目。

## 当前发行 v0.7.0

本版新增资料元标签、显式关系、上游反馈、按月归档与恢复；操作见[资料治理](playbooks/10-资料关联与月度归档.md)。验收见[回执](docs/资料归档回执/README.md)。

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

## v0.6.2：进化史闭环整合

保留主线轻量纪律，整合已验收的 CLI 和只读进化史。操作入口：[最小闭环](playbooks/09-最小完工闭环.md)。固定产物 `docs/东合/`，完工回写日志与交接、共享指纹、历史失败直达；不要求补建九类空文档。

需 Python 3.9+（macOS/Linux），无第三方 Python 包或前端构建。资料关联与归档已实现；图谱自动安装、运行时打包正在下一阶段开发。

提交后执行 `scripts/sync.sh`，只分发运行所需文件；安装前备份到 `~/.donghe/backups/`，原安装定制也随备份保留。安装结果用目录内容对照和 CLI 启动检查验证。重新开启 agent 会话加载新版。

[32 项核心与 12 项浏览器证据](docs/证据改进回执/README.md)；这些是此前验收，整合后的补充验证见发行回执。

## v0.6.3

历史完工记录的证据失效不代表新施工授权；CLI 停止并请求复核，进化史单独标注历史待复核。[双项目证据](docs/双项目纵切回执/README.md)。
