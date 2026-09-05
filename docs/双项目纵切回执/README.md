# 双项目纵切暴露的队列边界

2026-09-05。只在隔离候选源码修复，不改已发行 v0.6.2，不推送或同步全局技能。

DHS Forms 新任务通过后，原版 next 仍返回 work。原因是旧 completed 卡在克隆路径变化或输入漂移后，证据判定重新变为 active；旧 authorization 又被当作新施工授权。

修复保持 closed 严格校验：旧证据仍 stale/invalid；只将正本 active 卡放入施工队列，已声明完工而缺当前有效证据者 stop 并待显式复核。显式 verify 可重开。界面显示“历史完工记录 · 当前证据待复核”，不计入进行中数量。

[原版真实读取](queue-before.json)与[候选真实读取](queue-after.json)针对同一数据，不编辑旧回执或重跑旧任务来造结果。[核心34项](queue-tests.txt)；[旧版反证](queue-red.txt)区分真实行为失败与测试建设期错误。[Forms进化史浏览器验收](evolution-forms-result.json)。

完整双项目开发、iOS与原仓隔离回执位于 `/Users/aiden/dev/donghe-dual-pilot/README.md`。本修复不是历史迁移或归档实现；不得用放宽完成校验来解决跨目录历史识别。
