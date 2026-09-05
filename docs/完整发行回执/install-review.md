# 完整发行安装包 · 独立审查

## 裁定

**macOS arm64 首版可验收。** 初始 59 MiB RC 样包暴露的两个 P1 均已修复；重建后的正式 `v0.7.0` 包在 Darwin arm64 上通过双目标安装、失败全量回滚、内容与 mode 篡改拒绝、最小 PATH 首用和内置图谱启动。

审查只覆盖 macOS arm64，没有推断或声明跨平台支持。

## P1（已修复）· 第二目标失败不会回滚第一目标

位置：`scripts/install_release.py:46-65`。

安装器先验证所有目标字符串，之后逐目标完成 staging、旧目录备份和 `os.replace`。已完成目标没有纳入跨目标事务；后续目标在 `target.parent.mkdir`、staging、验证或替换阶段失败时，函数直接退出，之前的目标保持新版本。

隔离复现使用两个显式目标：第一个是正常临时目录，第二个目标的父路径预先创建为普通文件，使失败稳定发生在第二轮 `target.parent.mkdir`：

```text
two-target-exit 1
FileExistsError: [Errno 17] File exists: '.../blocked'
target1-installed True
target2-installed False
```

这会让默认 Codex/Cursor 双端安装产生版本分裂。虽然第一目标的旧副本仍留在 backup 中，但安装命令没有自动恢复，输出也没有结构化说明部分提交状态。

修复结果：安装器现在先为全部目标完成 stage、manifest 校验和双 healthcheck，再开始切换。提交失败会逆序回滚全部已切换目标；旧安装恢复原位，本轮原本不存在的目标不直接删除，而是移动到本轮 backup 的 `*.failed` 保全。`install.json` 记录 committed、rolled_back 或 rollback_failed 及逐目标恢复状态。backup root 的相对路径、symlink 和与目标重叠也会拒绝；临时清理只遍历本轮记录的 stage root。

正式包第二目标提交后注入失败的真包结果：

```text
exit 1 injected True
first-restored old bytes
second-absent True
receipt-state rolled_back
rolled-back 2
failed-kept [True, True]
```

## P1（已修复）· manifest 不校验执行位，图谱损坏仍安装成功

位置：`scripts/build_release.py:79-90`、`scripts/install_release.py:18-33`、`scripts/install_release.py:53-57`。

manifest 对普通文件只保存 SHA-256，不保存 mode。安装校验因此无法发现二进制执行位丢失；stage smoke 又只执行 `bin/donghe --help`，没有执行随包的 `runtime/codegraph/codebase-memory-mcp`。

样包原始图谱文件为 mode `0755`，`PATH=/usr/bin:/bin runtime/codegraph/codebase-memory-mcp --version` 返回 `codebase-memory-mcp 0.8.1`。在临时解包副本仅执行 `chmod a-x` 后，文件内容哈希未变，随后运行 `bin/install`：

```text
manifest-graph-entry {'sha256': '595cedd259200424f3d92b04e116dc2de4d75bc38ed6a615205ab51ee61de485'}
nonexec-graph-install-exit 0
installed True
```

安装结果中的图谱二进制不可执行。用户会得到成功回执，但首次使用内置图谱时失败。

修复结果：正式 manifest 为每个普通文件登记 mode，安装器同时校验 SHA-256 与 `stat.S_IMODE`；stage healthcheck 在 `PATH=/usr/bin:/bin` 下执行 `bin/donghe --help` 和图谱 `--version`。正式包的 graph mode 为 `0755`；临时去掉执行位后安装返回 1，目标未创建，错误明确为 mode mismatch。

## 已验证事实

- 正式 `v0.7.0` 包外层 SHA-256 与 sidecar 一致：`00ee8a31dfef18b0fe9c00ed9b558ab05eaaba0be859ab0a5dc1aaf4dda0bf61`。
- manifest 平台是 `darwin-arm64`，共登记 1706 个文件/链接；锁定 CPython 3.12.14 与 codebase-memory-mcp 0.8.1。
- 原样解包后，在 `PATH=/usr/bin:/bin` 下 `bin/donghe --help` 返回 0；图谱 `--version` 返回 0。
- 安装到临时目标会保存既有目录；备份中的旧文件字节可读，安装目录含 manifest。
- 安装后在 `PATH=/usr/bin:/bin` 下对全新临时项目运行 `bin/donghe --project <fixture> init` 返回 0，并输出有效项目状态 JSON。这证明首用不依赖系统 Python 或用户 PATH 中的第三方命令。
- 修改临时包的 `README.md` 后安装返回 1，目标未创建，错误为 file checksum mismatch。
- 把目标父目录改成 symlink 后安装返回 1，错误为 invalid target，未跟随链接写入。
- `build_release.extract` 静态检查绝对路径、解析后越界、设备/FIFO，以及越界软/硬链接；安装器再次比对 manifest 的完整路径集合和链接目标。未发现现有样包包含越界链接。
- 安装器定向单元测试 5/5 通过，覆盖 mode 漂移、全目标预检、第二目标提交故障回滚、旧安装备份和 backup root 边界。

## 未验证与复验要求

本次没有重跑全 suite，也没有修改真实 Codex/Cursor 技能目录。正式 tarball 已由父任务重建并完成上述真包复验。当前证据只支持 macOS arm64；其他平台、代码签名/notarization、跨磁盘 backup 与真实磁盘耗尽下的 rollback_failed 恢复仍未验证，不应写入首版支持声明。
