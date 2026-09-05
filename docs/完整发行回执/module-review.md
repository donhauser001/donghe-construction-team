# v0.8.0 候选模块独立审查

## 裁定

**模块候选可验收。** `donghe_graph.py`、`donghe_adopt.py`、`donghe_ui.py` 与主 CLI 接线的基础路径真实。初审三个 P1 及后续发现的 WebSocket 绕过均已修复，并由原始独立反证复验闭合。此结论只放行模块候选；`v0.8.0` 仍须以稍后真正重建的完整包另做发行验收。

本轮只读核心代码，所有复现均在 `tempfile.TemporaryDirectory` 和临时 HTTP 服务中完成。没有修改核心、没有运行全 suite、没有提交。`output/release/donghe-construction-team-v0.7.0-darwin-arm64.tar.gz` 仅是完整包试制品；本回执不称其正式发布，也不替代稍后 `v0.8.0` 真包验收。

## P1（已闭合）· 任意非空文件可让图谱状态显示 current

位置：`scripts/donghe_graph.py:138-176`、`scripts/donghe_graph.py:274-293`。

`refresh` 将上游生成的数据库留在 generation cache，但 metadata 不记录数据库路径、大小或 SHA-256。`status` 只核对 current metadata 与 generation metadata 完全相同、源 fingerprint 相同，并要求 `cache/*.db` 至少有一个非空普通文件；它不证明 DB 是本 generation 的图谱产物。

隔离复现创建一个 `app.py`，计算真实源 fingerprint，再手写相同的 `current.json`/`generation.json`，把 `cache/unrelated.db` 写成 `not a graph database`，并把 projectName 写为 `different-project`。结果：

```text
state: current
fresh: true
projectName: different-project
fingerprint.files: 1
```

这不是同一账号恶意篡改才会出现的问题：DB 被截断后仍非空、目录误拷贝或 metadata/DB 跨 generation 混配，都会把不可查询的产物显示为 current。随后 search 才降级为 `query-failed`，状态页和调度在查询前已经 false pass。

修复结果：refresh 现在把数据库相对路径、size 与 SHA-256 inventory 写入 generation metadata；status 对实际 DB 集合与 inventory 做完全比对，并以 SQLite 只读方式运行 `PRAGMA quick_check`，核对 `projects/file_hashes/nodes/edges` 表、projects schema，以及唯一 `(projectName, snapshotRoot)` 绑定。测试 fixture 改为真实最小 SQLite schema，新增未登记 DB、缺 inventory 和项目绑定不一致的否定用例。

原始反证不变重跑后结果为：

```text
{"state": "stale", "fresh": false, "projectName": "different-project"}
```

对应定向测试 `test_uninventoried_or_unbound_database_cannot_make_graph_fresh` 1/1 通过。真实 Forms/medical 结果记录于 `real-codegraph.json`：分别索引 966/888 个文件，graph 查询返回且有已验证源码位置，未变化 refresh 复用当前 generation。该回执仅保留计数与身份，不含源码或完整图数据。

## P1（已闭合）· localhost 顶层页面可向未授权 URL 发请求且带异常通过

位置：`scripts/donghe_ui.py:151-155`、`scripts/donghe_ui.py:251-288`。

URL 边界只在导航后与每个步骤后读取顶层 `location.href`。页面的 fetch、XHR、图片、脚本、iframe 和 WebSocket 没有 CDP 网络拦截。`Runtime.exceptionThrown` 与日志只在 finally 收集，不影响最终 `status`。

真实浏览器复现使用两个临时服务：授权页面位于 `http://127.0.0.1:<page>/`；按钮点击后执行 `fetch('http://0.0.0.0:<sink>/unauthorized', {mode:'no-cors'})`，异步抛出 `Error('boom')`，同时把断言文本改成预期值。结果：

```text
status passed
steps [('click', True), ('assert_text', True)]
hits ['/unauthorized']
exceptions 1
error None
```

顶层 URL 从未离开 127.0.0.1，因此现有检查没有拦截。这意味着一个标为 localhost-only 的验收步骤可产生白名单外网络副作用；页面运行时明确异常也可获得通过回执。

修复及原反证结果：适配器固定初始 origin，导航前启用 CDP `Fetch`，只继续同 origin HTTP(S) 和明确的 `data:`/`blob:` 请求；结束前等待并收集事件。默认 uncaught exception 和 `networkViolations` 均阻止 passed。原始 fetch+throw fixture 加入有效 authorization 后复跑，得到 `status=failed`、sink hits 0、violations 1、exceptions 1，证明不是被前置 contract 校验挡住。

但 `Fetch` 不拦截 WebSocket 握手。新增真实浏览器反证：授权 127.0.0.1 页面点击后执行 `new WebSocket('ws://0.0.0.0:<TCP sink>/leak')`，随后 DOM assertion 通过。结果：

```text
status passed
sink hits 1
networkViolations 0
exceptions 0
```

TCP sink 收到真实 WebSocket HTTP Upgrade 握手。因此当前实现只能证明 fetch/普通子资源边界，不能宣称完整 URL 边界。修复需在握手发出前阻断跨 origin WebSocket；仅监听 `Network.webSocketWillSendHandshakeRequest` 再把结果改 failed 可以关闭 false pass，却不能满足“未授权请求没有离机”。应增加真实 WebSocket sink 回归，同时保留原 fetch+exception 反证。

第二次修复尝试加入 `Network.setBlockedURLs` 与 `Network.webSocketCreated` 诊断后，一度只能事后判 failed，握手已经命中 sink。最终实现对 Document response 保留原 headers 并追加更严格的 `Content-Security-Policy: connect-src ...`；多份 CSP 取交集，不放宽页面原策略。`Network.setBlockedURLs` 同时覆盖默认/显式端口的 HTTP(S)/WS(S)，security CSP 日志中的被阻断连接被结构化写入 `networkViolations`。`allowedOrigins` 最多 10 个，只接受无 path/query/userinfo 的本机 origin；顶层导航仍固定初始 origin。

独立原始攻击 fixture 使用普通 TCP sink，最终结果：

```text
#ws    status failed sink 0 violations 1 exceptions 0 pointer trusted CDP pointer
#fetch status failed sink 0 violations 1 exceptions 1 pointer trusted CDP pointer
```

这证明 WebSocket 握手和 fetch 都没有抵达未授权端口，且回执非 passed；正常真实指针/文本 GUI 用例仍通过。定向执行 WebSocket、fetch+异常、真实 GUI 三项，3/3 通过。作者锁定 Chrome 的完整 UI 模块结果为 13/13，其中显式授权的第二本机 API origin 真请求命中 1 次且通过。

真实性边界：这是受控 headless Chrome 的 CDP+CSP 浏览器边界，不是 OS 级网络沙箱；启动参数明确关闭 LNA/PNA 检查以让本地多端口行为由上述确定性合同接管。该结论不能外推到普通用户浏览器或任意进程网络隔离。

## P1（已闭合）· UI 回执不能证明执行了哪些授权步骤

位置：`scripts/donghe_ui.py:205-213`、`scripts/donghe_ui.py:260-277`、`scripts/donghe_ui.py:300-317`，主 CLI 接线 `scripts/donghe.py:759-760`、`scripts/donghe.py:794-795`。

UI contract 没有 authorization 字段。`result.json` 不保存原始 contract、canonical hash 或 spec 文件 hash；逐步结果只保存 index、action、passed、duration、URL 与运行 detail。click 的 detail 只是 `{ok:true}`，因此回执不含 selector；fill/assert 的预期字段也没有作为不可变请求身份保存。spec 文件位于项目任意位置且执行后不登记，可被修改或删除。

因此两个完全不同、外部影响不同的点击都会留下无法区分的“click passed”。现有 CLI 非零退出能证明当次 adapter 自报失败，却不能证明回执对应用户授权的 URL、选择器、输入值与断言，不能作为可审计的 UI 验收证据。

修复结果：contract 现在要求非空 authorization，保存 canonical contract 与 SHA-256；步骤回执保留 action、selector、assert text、截图名和非秘密 fill 值。`secret:true` 的 fill 只保存 `valueSha256`，明文不进入 canonical contract 或步骤回执。点击与输入改走 CDP Input，截图保存路径与 SHA-256；至少含一个 assert 才能 passed，纯截图结果为 captured。UTC `startedAt/finishedAt`、`Project.path` 与 evidence symlink 边界均已接入。

独立复跑原攻击 fixture 时，`contractSha256` 为 64 位且 click selector `#go` 保存在步骤身份中。官方锁定 Chrome 下 `tests.test_ui` 10/10 通过，覆盖授权/脱敏、真实输入、同源导航、fetch 拦截、异常、captured 与证据路径边界。

## 已验证基础

执行相关测试：

```text
python3 -m unittest tests.test_graph tests.test_adopt tests.test_ui tests.test_product_integration -v
Ran 27 tests in 8.659s
OK
```

已确认：

- graph 源 fingerprint 覆盖文件名、字节、删除与未提交改动；排除 `.donghe`、治理产物、常见构建目录和明确秘密文件。源 symlink、state symlink、覆盖预算和版本不匹配均拒绝。
- refresh 使用独立 generation、源快照和隔离 config；失败保留旧 current，源在构建期间变化或没有 DB 时不发布 current。search 在 missing/stale/query failure 时明确降级，trace 不把 stale 图谱冒充可用。
- adopt 只读取项目内 UTF-8 Markdown，拒绝 `..`、治理目录、secret 目录、symlink、缺失与超预算文件；登记稳定 ID、路径和 SHA-256。源变化会显示 stale，重新采纳要求显式 update。manifest 写失败后的再次 adopt 可恢复而不复制记录。
- registered source API 只按登记 ID 查找，重新走路径、symlink、文件类型和大小校验；不会把 ID 参数直接当路径。主 CLI 的 `source` 同样只接受登记身份。
- UI 顶层初始 URL 和导航离开 localhost 会失败；无浏览器、断言失败和无效 contract 返回非零并保存 failed/unavailable result；截图名称拒绝目录逃逸。
- 主 CLI 对 graph/adopt/source/feedback 使用项目锁，UI 结果非 passed 时退出非零；`/api/registered-source` 与 `/api/source` 均返回 JSON，前端现有渲染路径使用文本节点而非把来源正文注入 HTML。

## 发布阻断与复验

本轮模块门禁已放行，但这不等于 `v0.8.0` 已发布或完整包已验收。应使用真正重建的 `v0.8.0` 包在 macOS arm64 复验 graph refresh/search/stale、adopt/source stale、UI 成功、允许的本机多端口请求与 WebSocket/fetch 越界失败；不要沿用当前 v0.7.0 试制 tar 作为发布证据。
