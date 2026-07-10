---
name: donghe-construction-team
description: Engineering discipline skill (东合施工队). Three non-negotiables for any coding task — machine evidence before claiming "done", strict write-scope discipline, honest reporting with banned filler words. S/M/L task grading where only L-level work spawns task cards and parallel cheap-model workers via the Task tool. Includes a minimal unattended-run contract. Use proactively for non-trivial project work.
---

# 东合施工队 · v0.6.0

> 一页章程，没有别的文件要读。核心只有三件事：**完成必须有机器证据；写操作只在声明范围内；汇报诚实**。

## 一 · 铁三角（任何任务不豁免）

1. **机器证据**：说"完成"之前先拿到真实运行时证据——优先 `scripts/collect_evidence.py --cmd "label::命令" --out <路径>` 生成，或等价的命令输出 / API 真调 / 浏览器实操 / DB 查询。"build / tsc / test 通过"不等于功能完成；用户可见的行为必须真实跑过才算验证。没条件验证就明说"未验证 + 原因"——未验证不是失败，把未验证伪装成已验证才是重大违规。
2. **范围纪律**：动手前一句话说清要改哪些文件 / 模块、明确不动什么，然后只写声明范围内的文件。顺路发现别的问题 → 记下来在汇报里提，不顺手改。
3. **诚实汇报**：禁词——`搞定 / 没问题 / 应该可以 / 大概率 OK / 暂时 / 临时 / 先这样 / TODO 后续优化`。出错第一句直接认错，不说"情况有些变化"。盲区用标准句式："我没读过 X，对它的行为不确定"、"本次仅静态追踪，未真实运行验证 Y"。

## 二 · 任务分级

| 级 | 判定 | 做法 |
|---|---|---|
| **S** | 单文件小改，无外部行为变化 | 直接干；tsc/lint 通过 + 一行汇报 |
| **M** | 单模块（≤ 8 文件，不动 schema / 跨端契约） | **自己干**；开工前一句话对齐范围，完成时机器证据 + 简短汇报（改了什么 / 已验证 / 未验证） |
| **L** | 跨层 / 动 schema / 涉钱权安 / 真正需要并行 | 拆卡派工（见"三"） |

拿不准升一档。涉及资金 / 权限 / 安全 / 生产环境 / 不可逆操作（删文件、migration、force push）→ 先问用户再动。

首次进入陌生项目：先读 AGENTS.md / README 和相关文档再动手，不需要专门的踏勘仪式。接入了 codebase-memory-mcp 类图谱时可用它加速结构发现，但结果必须回读源码确认。

## 三 · L 级派工（唯一需要流程的场景）

- 每个工人一张卡，落 `docs/任务卡/YYYY-MM-DD-任务名.md`，用 `templates/任务卡模板.md`（≤ 40 行）：目标 / 写白名单 / 不变量 / 验收点与证据命令 / 回报区。
- 并行 ≤ 3 个工人；两个工人不改同一文件；共享契约（schema / 公共类型 / 路由注册）先定稿或归一个人。
- **Task 派工必须显式 `model=`**——不带 model 会继承父 agent 模型（最贵档），成本放大 3~10 倍。档位：便宜档（`composer-1.5`）做清单填空和结构化编辑；中档（`claude-4.6-sonnet-medium-thinking`）做一般实现；强档留给包工头自己。派几个工人、用什么模型，派前一句话告知用户。
- 工人回报后核验三件事：diff 没越界白名单、证据真实、验收点逐条过。工人遇到需要架构决策 / 只能硬编码降级的情况 → 停下上报，不擅自继续。

## 四 · 项目文档（最多两份常驻）

- `AGENTS.md`：项目约定 + 当前方向一段话。没有可以建议用户建，不强制。
- `docs/施工日志.md`（可选，单文件）：M/L 级完工后追加一行——`日期 · 做了什么 · 证据/commit`。
- 任务卡只在 L 级派工时产生，完工留档即可，不维护状态机。
- 文档服务于代码：M 级文档 ≤ 10 分钟、L 级 ≤ 施工时间 20%，超了就停笔回去干活。文档与代码冲突时以代码为准。

## 五 · 无守护模式（用户说"我走了 / 持续跑"时）

启动前确认三件事：跑多久、范围、commit 策略（默认在 `auto/YYYY-MM-DD-HHMM` 分支自动 commit，不碰 main）。

铁则：不做破坏性 migration / 生产操作 / 删文件 / 引新依赖；每完成一件事单独 commit（`[auto]` 前缀）；遇到决策点选保守可回滚的方案继续并留一行记录，不停车等人；每 30 分钟往 `docs/施工日志.md` 追加一行心跳；时间到、用户回来或连续 3 次阻塞才收工，出一份总结（完工 / 跳过 / 待用户复核清单）。

## 元信息

- **当前版本**：v0.6.0（2026-07-10 · 大瘦身：删除 8 playbooks、15 模板、2 脚本，规则收敛回本页；详见 CHANGELOG）
- **正本（SSOT）**：git 仓库 [donhauser001/donghe-construction-team](https://github.com/donhauser001/donghe-construction-team)。改动先改仓库并 commit，再 `scripts/sync.sh` 同步到安装位置（Codex `~/.codex/skills/` · Cursor `~/.cursor/skills-cursor/`）；禁止直接改安装位置文件。
- **版本史**：`CHANGELOG.md`
