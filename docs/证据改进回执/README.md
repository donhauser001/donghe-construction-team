# v0.6.1 证据改进实测

2026-09-05，隔离分支 `codex/evidence-history`，基线 `6ef9fc1ac9d79394ce5d7dddcac798a922792a43`。依据 DHS Forms 真开发暴露的问题实施；不是新一轮模型评分。

新回执使用按内容寻址的共享指纹，旧回执原样读取。共享对象缺失或损坏使引用回执失效。CLI `receipt` 和界面优先提供有界摘要，完整回执与输出按需读取；历史失败直达，历史结果与当前验收分别呈现。退出码零但报错或输入变化时不显示通过。

## 验证

[核心测试](core-tests.txt) 32/32；[浏览器验收](evolution-ui-result.json) 12/12，包含真实旧回执、三次历史失败、手机宽度、焦点恢复和无效数据反证。[历史失败截图](historical-failure.png)。

[存储对照](storage.json)：九份旧回执及配套文件 2,940,068 字节，临时目录重编码后（包含共享对象和完整输出）678,667 字节，减少76.92%，四个唯一指纹对象。规范化内容等价、输出字节保留、源目录前后哈希一致。

这是同数据编码实验，不是生产迁移或模型 token 测量，没有重跑九次验证，原项目文件体积未改变。

复现：`python3 evals/pilot/evidence-storage.py /Users/aiden/dev/dhs-forms-pilot --output output/evidence-review/storage.json`；核心：`python3 -m unittest discover -s tests -v`。浏览器 `evals/pilot/evolution-ui.cjs` 接受 URL、Playwright 模块路径、Chromium 路径、输出目录四参数；报告区分真实项目和隔离夹具。

## 交付与回滚

只在隔离 worktree 修改，未同步全局技能或推送合并。4320 查看器只读旧试点；未改 DHS Forms 原项目或证据，未重跑 Forms 业务提交或 Docker 生命周期测试。

可使用未改的 `/Users/aiden/dev/donghe-first-slice` 基线，或对本分支独立提交执行 git revert。v0.6.0 不认识 v2 回执；后续若产生新版证据，应保留 v0.6.1 读取器和共享对象，源码回滚不等于数据降级。本轮没有转换旧证据，暂无降级需求。

未实现共享对象垃圾回收、历史迁移、月度归档、图谱生命周期和完整安装包。哈希与只读属性是误改检测，不是同一 OS 用户的安全隔离。当前授权范围完成，无新目标停止。
