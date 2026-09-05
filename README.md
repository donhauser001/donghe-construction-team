# 东合施工队 · 东合项目进化史

一个安装单元，把施工纪律、真实验收、资料演进、代码定位和只读进化史连接起来。当前完整包 **v0.8.0，macOS arm64**；Codex 与 Cursor 共用同一份技能与 CLI。

## 安装与触发

从 [GitHub Releases](https://github.com/donhauser001/donghe-construction-team/releases) 下载平台完整包，解压并运行 `donghe-construction-team/bin/install`。默认安装到 Codex/Cursor 技能库，自动备份旧版；无需另装 Python、Node、图谱或浏览器。首次触发从技能自己的 `bin/donghe --project <绝对路径> doctor` 自检，再 init。

包内 Python 3.12.14、codebase-memory-mcp 0.8.1、Chrome Headless Shell 152.0.7977.82 均锁定地址、摘要和许可，首用不下载。安装要求 macOS arm64 本地终端与文件权限；其它平台未做完整包验收，不按源码能启动就宣称支持。

## 已实现的闭环

- 任务有明确来源和验收契约；真实命令回执验证后，finish统一更新任务、当日日志与交接。
- 未来池、蓝图、路线图、近期方向、技术债和知识按需记录；显式关系、上游反馈，与任务形成九类资料链，不预建空文档。
- 进化史按任务历程和方向关系呈现，原文与回执按需展开，归档后继续读取。
- 月度首次开工/完工触发历史归档检查；无后台空跑，可中断续做、多轮归档和恢复。
- 有据接入旧资料，保留原文件及摘要，来源变化标陈旧；没有事实不补造历史。
- 图谱隔离到当前checkout的.donghe目录，新代成功才切换；源未变复用，陈旧则精确搜索降级，调用链回读源码。
- 包内浏览器支持真实Web步骤与断言；截图不能替代行为验收。原生移动端仍用宿主原生环境。

固定产物在 `docs/东合/`，本机缓存与操作状态在 `.donghe/`。不要把缓存当业务源码提交。

## 操作入口

[完工闭环](playbooks/09-最小完工闭环.md) · [关联归档](playbooks/10-资料关联与月度归档.md) · [完整包与代码定位](playbooks/11-完整包与代码定位.md) · [真实界面验收](playbooks/12-真实界面验收.md)。CLI提供help，复杂模式按需读；不要求每次加载全部技能文档。

## 维护与发行

本仓库是唯一源码正本，安装目录是分发产物。修改后完成相关行为验证并提交，再构建：

```sh
python3 scripts/build_release.py --cache /absolute/download-cache --out output/release
```

这是作者构建命令，不是用户首用依赖。构建完整包含必要运行时、文件清单与SHA256 sidecar；`bin/install` 全目标预检后切换，失败整体回滚并保留失败包。`scripts/sync.sh` 仅作为完整包安装兼容入口，不再用源码覆盖已安装运行时。

[发行实测](docs/完整发行回执/README.md)记录已验证范围、原项目保护与局限。[版本史](CHANGELOG.md)保留旧回执口径；测试绿灯不代表所有业务项目自动正确。

## 许可

东合源码 [MIT](LICENSE)。第三方各自许可随完整包保留；版本来源见 `packaging/components.lock.json`。没有上传源码或调用云索引的步骤；本地浏览器不是操作系统网络隔离沙箱。
