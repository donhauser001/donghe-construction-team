# 63fcab7 资料关联与月度归档候选 · 独立审查

## 裁定

**仍不可验收。** 路径边界、同名冲突、归档字节校验、中断续跑、冷读取、反馈幂等及只读前端的数据来源均有真实实现和定向测试支撑。下述 P1-1 已修复并回归；P1-2 仍由主审处理，处理完成前当前候选不应发布。

## P1（已修复）· 恢复后的未改资料无法再次归档

位置：`scripts/donghe_archive.py:221-233`、`scripts/donghe_archive.py:268-296`。

归档操作 ID 只由 `kind + month + candidates` 决定。资料归档、恢复后若字节未变，下一次归档生成完全相同的 ID；`_operation` 读回首次已提交 journal，其中 `moved` 已包含源路径。此时源文件合法地回到 live、冷目标不存在，但 `_move` 把这个新一轮状态解释成旧操作损坏并报错：

```text
first-plan 1
archive-1 ... state=committed count=1
restore-1 ... state=committed count=1 integrity=archive-journal
second-plan 1
archive-2-error ValueError recorded move no longer matches storage: docs/东合/资料/idea/I1.md
live-exists True cold-exists False
```

复现仅创建一份 2020-03 closed idea，依次调用 `plan/apply/restore/plan/apply`，全程在 `tempfile.TemporaryDirectory` 中完成。它违反 playbook 10 的“归档任务先 restore，再在明确授权下重开”以及可恢复长期维护语义：恢复查看后不修改内容是正常路径，却使该资料永远不能再次进入冷档案。

修复结果：新操作加入随机 `attemptId`，操作 ID 对每轮独立；`_pending` 仍只续跑 `prepared` journal，已提交的相同候选会创建新轮次。校验器同时接受没有 `attemptId` 的旧确定性 journal，历史日志不迁移、不覆盖，仍可用于恢复 provenance。回归覆盖 archive → restore → archive → restore、第二轮归档和恢复分别中断后续跑、四份历史日志字节不变，以及旧 journal 恢复仍返回 `archive-journal`。

## P1 · 一份坏元标签让每月首次 init 部分写入后永久失败

位置：`scripts/donghe.py:103-108`、`scripts/donghe.py:582-596`，`scripts/donghe_archive.py:189-203`、`scripts/donghe_archive.py:342-345`。

`plan` 会把任意活动资料中的坏 `donghe-meta` 记入 `issues`；`maintain` 随后无条件调用 `apply`，而 `apply` 拒绝所有带 issue 的计划。由于 `init` 先创建目录和交接文件，退出锁后才做维护，最终表现为命令非零但项目已经部分改变；maintenance marker 没有写入，因此之后每次 init 都继续失败。

隔离 CLI 最小复现：在全新项目只放 `docs/东合/资料/idea/BAD.md`，内容为坏 JSON fence，然后执行 `python3 scripts/donghe.py --project <绝对临时目录> init`：

```text
exit 1
stderr {"error": "cannot apply an invalid archive plan"}
handoff-created True
maintenance-marker False
```

这与 playbook 10 的“坏元标签有诊断，不静默推断修复”和“只移动符合条件的关闭资料”不相称。坏活动资料应留在 live 并暴露诊断；它不应让初始化或一次已经完成的 `finish` 对调用者报成整体失败。当前 `finish` 也在完工事务与反馈落盘后调用同一 `maintain`，因此会产生“CLI 报错，但任务实际已完成”的外部身份分裂。

修复建议：将维护检查结果与归档可执行性分开。自动 `init/finish` 遇到诊断时应记录本月已检查、返回结构化 maintenance issues，并跳过归档移动；显式 `archive --apply` 可继续严格拒绝。若产品选择让诊断阻断入口，则必须在任何 init/finish 写入前预检，确保失败不留下部分状态。补集成测试覆盖坏 active record 下 init、已验证任务 finish、重复调用和 status/UI 诊断。

## 已验证的基础

执行：

```text
python3 -m unittest tests.test_archive tests.test_records tests.test_governance_integration -v
Ran 29 tests in 0.553s
OK
```

这些测试证明多轮归档/恢复、两轮中的中断恢复、旧 journal 兼容、同名与哈希冲突拒绝、符号链接拒绝、冷目录读取、任务反馈幂等、derived_from 因果边界及新 reader 无缓存读取均通过。源码追踪确认 `/api/state` 每次调用 `project.status()`，前端 fetch 使用 `cache: no-store`；资料和任务原文按钮仍传逻辑路径，由 `Project.path → archive.resolve` 定位冷副本。未发现前端把 `relates_to` 展示成因果完成、把 archived 自动显示为当前验证通过，或直接写派生状态。

## 未验证与发布建议

本次未重跑作者的完整 Playwright/Chromium 验收；用户可见界面的已有证据只作为候选佐证，不替代上述源码与隔离复现。未运行全仓无关测试。P1-2 修复后，应重跑上述 28 项定向测试、主审新增集成回归，以及归档后刷新、原文打开、恢复再归档的真实浏览器链，再决定发布。

### 主审收口

P1-2已按建议修复并新增实际init/finish/重复维护/严格手动拒绝测试。63项本版全量回归通过；已restore夹具再次完成12项浏览器归档链。两个发布阻断均关闭，可发行v0.7.0。本结论不追溯改变上面的原始失败证据。
