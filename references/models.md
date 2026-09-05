# 模型档位

SKILL 正文只要求一件事：Task 必须显式 `model=`，禁止 `inherit`。

本表是快照。**以当前会话宿主给出的模型列表为准**——列表里没有的 slug 不要用，宁可先问用户，也不要猜一个过期名字。

## 怎么选

| 档 | 用途 | 选法 |
|---|---|---|
| 便宜 | 清单填空、结构化编辑、按卡改白名单内文件 | 宿主列表里带 `fast` 的最便宜编码模型 |
| 中 | 一般实现、需要判断但不需要架构拍板 | 中档；不要为了「更聪明」默认上最强档 |
| 强 | 包工头自己：拆卡、核验、架构决策 | 父 agent 当前模型，不派 Task |

派前一句话告诉用户：派几个、用哪档、slug 是什么。

## Cursor（2026-08-24 快照）

当时 Task 工具可见：

| slug | 档 |
|---|---|
| `inherit` | **禁止** |
| `composer-2.5-fast` | 便宜档默认 |
| `gpt-5.5-medium` | 中档可用 |
| `cursor-grok-4.5-high-fast` / `cursor-grok-4.6-high-fast` | 备选，偏快 |
| `gpt-5.6-sol-medium` / `kimi-k3-max` | 仅当任务明确需要时 |

## Codex

以当前 Codex 会话的模型列表为准。不要把旧 slug 写进任务卡（包括 `composer-1.5`、`claude-4.6-sonnet-medium-thinking`）。

若列表同时有 fast 与 thinking：清单填空用 fast，一般实现用中档 thinking。
