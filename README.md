# 东合施工队 (Donghe Construction Team)

一套面向 AI coding agent 的**工程闭环 skill**：让 agent 以"施工队"而不是"单兵实现者"的方式承接非平凡的编码工作。包工头（强模型父 agent）负责踏勘、拆卡、派工、审计、验收；施工工人（便宜档子 agent）按任务卡施工；全程以真实运行时的机器证据为完成标准。

当前版本见 `SKILL.md` 标题与 `CHANGELOG.md` 最新条目。

## 核心机制

- **S/M/L 改动半径分级**：半径决定流程强度，可升不可降。
- **任务卡落盘 + 三元绑定**：M/L 级工作必须在项目 `docs/任务卡/` 落盘瘦身任务卡，强制"派给 / 调用模型 / 匹配理由"三字段。
- **并行编排**：默认 ≤3 工人；六槽作战编制（文书 / 巡检 / 审计 / 3 工人）；用户授权可分波爆发到 8 工人。
- **工人不空转 / 审计异步**：审计与施工解耦，小问题回炉、大问题转新卡。
- **机器证据**：完成宣称必须有真实运行时证据，优先用 `scripts/collect_evidence.py` 生成。
- **无守护自动施工**：时间契约、不空跑、决策不中断、技术债账本、蓝图自动下放、临时分支推送隔离。
- **代码图谱辅助**：接入 codebase-memory-mcp 类知识图谱时作为结构发现加速器（结果必须回读源码）。

## 目录结构

| 路径 | 内容 |
|---|---|
| `SKILL.md` | 总章程（按主题组织的现行规则 + 场景路由表） |
| `playbooks/` | 20 个场景 SOP（接活契约、任务卡规范、并行编排、无守护模式等） |
| `templates/` | 任务卡、踏勘报告、施工日志、技术债账本等模板 |
| `agents/openai.yaml` | Codex agent 入口（最小 prompt + 换道触发词） |
| `scripts/collect_evidence.py` | 机器证据采集（git 状态 + 验证命令输出 → Markdown） |
| `scripts/sync.sh` | 仓库 → 本地安装位置的单向同步 |
| `scripts/lint.sh` | 内链存活 + 版本号一致性校验 |
| `CHANGELOG.md` | 版本演化史（正文不按版本组织，历史一律查这里） |

## 安装

本仓库是**唯一正本（SSOT）**。克隆后同步到 agent 的 skill 目录：

```bash
git clone https://github.com/donhauser001/donghe-construction-team.git
cd donghe-construction-team
scripts/sync.sh
```

`sync.sh` 默认同步到：

- Codex：`~/.codex/skills/donghe-construction-team/`
- Cursor：`~/.cursor/skills-cursor/donghe-construction-team/`

如需其它位置，编辑脚本内的 `TARGETS` 数组。

## 修改流程

1. 只在本仓库内修改，**禁止直接改安装位置的文件**。
2. 版本号需同步更新四处：`SKILL.md` 标题、`SKILL.md` 元信息、`agents/openai.yaml`、`CHANGELOG.md` 新条目。
3. 运行 `scripts/lint.sh` 通过校验。
4. commit 后运行 `scripts/sync.sh` 下发到本地安装位置。

## License

[MIT](LICENSE)
