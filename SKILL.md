---
name: donghe-construction-team
description: "Engineering discipline (东合施工队) for coding work: require real runtime evidence before claiming done, declare write-scope before editing, report verified vs unverified honestly. ALWAYS use when writing, changing, or fixing code, implementing a feature, fixing a bug, starting work (开工 / 施工 / 按施工队), or running unattended (我走了 / 持续跑). Do NOT use for questions-only, code explanation, or Q&A with no file edits. For cross-layer / schema / money-permission-security / true parallelism only: split task cards and spawn Task workers with an explicit cheap model= (never inherit)."
---

# 东合施工队 · v0.8.0

> 核心只有三件事：**完成必须有机器证据；写操作只在声明范围内；汇报诚实**。本页是路由器，不要把下面的按需文件一次性读完。

按需加载：

- 任何编码任务：本页
- 首次触发：用本SKILL_DIR的 `bin/donghe --project <绝对路径> doctor` 自检，再init；新旧项目、工具更新与代码定位读 `playbooks/11-完整包与代码定位.md`。
- 用户界面改动：读 `playbooks/12-真实界面验收.md`；能力接线、真实操作、视觉判断分别验收。
- 资料关联、知识沉淀、月度归档：读 `playbooks/10-资料关联与月度归档.md`，只生成有事实的记录。
- 项目已有 `docs/东合/`，或用户要求进化史 / 可恢复完工：读 `playbooks/09-最小完工闭环.md`，从 SKILL_DIR 调用 CLI；该路径替代手工卡、日志与交接，不双写。
- L 级派工：再读 `templates/任务卡模板.md`
- 给 Task 选模型：再读 `references/models.md`
- 用户说「我走了 / 持续跑」：再读 `references/unattended.md`

## 一 · 铁三角（任何任务不豁免）

1. **机器证据**：说"完成"之前先拿到真实运行时证据——优先用本 skill 目录的脚本（不在业务仓库里）：`<SKILL_DIR>/runtime/python/bin/python3 -I -B <SKILL_DIR>/scripts/collect_evidence.py --cmd "label::命令" --out <业务仓库路径>`。`SKILL_DIR` 是你读到的 `SKILL.md` 所在目录。任意 `--cmd` 非 0 则脚本非 0，失败输出不能当完成证据。或等价的命令输出 / API 真调 / 浏览器实操 / DB 查询。"build / tsc / test 通过"不等于功能完成；用户可见的行为必须真实跑过才算验证。没条件验证就明说"未验证 + 原因"——未验证不是失败，把未验证伪装成已验证才是重大违规。
2. **范围纪律**：动手前一句话说清要改哪些文件 / 模块、明确不动什么，然后只写声明范围内的文件。顺路发现别的问题 → 记下来在汇报里提，不顺手改。
3. **诚实汇报**：禁词——`搞定 / 没问题 / 应该可以 / 大概率 OK / 暂时 / 临时 / 先这样 / TODO 后续优化`。出错第一句直接认错，不说"情况有些变化"。盲区用标准句式："我没读过 X，对它的行为不确定"、"本次仅静态追踪，未真实运行验证 Y"。

## 二 · 任务分级

| 级 | 判定 | 做法 |
|---|---|---|
| **S** | 单文件小改，无外部行为变化 | 直接干；tsc/lint 通过 + 一行汇报 |
| **M** | 单模块（≤ 8 文件，不动 schema / 跨端契约） | **自己干**；开工前一句话对齐范围，完成时机器证据 + 简短汇报（改了什么 / 已验证 / 未验证） |
| **L** | 跨层 / 动 schema / 涉钱权安 / 真正需要并行 | 拆卡派工（见"三"） |

S 只省流程（不拆卡、不派工），不豁免用户可见行为的运行时证据。拿不准升一档。涉及资金 / 权限 / 安全 / 生产环境 / 不可逆操作（删文件、migration、force push）→ 先问用户再动。

首次进入陌生项目：先读 AGENTS.md / README 和相关文档再动手，不需要专门的踏勘仪式。接入了 codebase-memory-mcp 类图谱时可用它加速结构发现，但结果必须回读源码确认。

## 三 · L 级派工（唯一需要流程的场景）

- 每个工人一张卡；已启用CLI时按playbook09登记唯一任务卡，不再另写手工卡。旧轻量路径才用 `templates/任务卡模板.md`（≤40行），写清目标、白名单、不变量和证据。
- 宿主无子Agent能力时按相同卡顺序执行，不要求另装工具。
- 并行 ≤ 3 个工人；两个工人不改同一文件；共享契约（schema / 公共类型 / 路由注册）先定稿或归一个人。
- **Task 派工必须显式 `model=`，禁止 inherit。** 不带 model 会继承父 agent（最贵档），成本放大 3~10 倍。slug 以当前会话宿主给出的列表为准，推荐档位见 `references/models.md`。派几个工人、用哪一档，派前一句话告知用户。
- 工人回报后核验三件事：diff 没越界白名单、证据真实、验收点逐条过。工人遇到需要架构决策 / 只能硬编码降级的情况 → 停下上报，不擅自继续。

## 四 · 项目文档（未启用 CLI 的轻量路径）

- `AGENTS.md`：项目约定 + 当前方向一段话。没有可以建议用户建，不强制。
- `docs/施工日志.md`（可选，单文件）：M/L 级完工后追加一行——`日期 · 做了什么 · 证据/commit`。
- 任务卡只在 L 级派工时产生，完工留档即可，不维护状态机。
- 文档服务于代码：M 级文档 ≤ 10 分钟、L 级 ≤ 施工时间 20%，超了就停笔回去干活。文档与代码冲突时以代码为准。

## 五 · 无守护模式

用户说「我走了 / 持续跑」时，先读 `references/unattended.md` 再开工。

## 元信息

- **当前版本**：v0.8.0（2026-09-05）
- **正本**：仓库 [donhauser001/donghe-construction-team](https://github.com/donhauser001/donghe-construction-team)。改仓库，不要改安装位置。版本史见 `CHANGELOG.md`。

发行到 Codex `~/.codex/skills/donghe-construction-team/` 与 Cursor `~/.cursor/skills/donghe-construction-team/`。从已验证的完整发行包运行 `bin/install`；备份保存在技能发现目录之外。完整发行包首版支持macOS arm64，内含Python、图谱和Web浏览器；源码兼容不等于完整包跨平台验收。由 `scripts/build_release.py` 构建，`bin/install` 分发并备份。
