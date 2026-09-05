#!/usr/bin/env bash
# 从仓库（SSOT）单向同步到 Codex / Cursor 本地安装位置。
# 用法：在仓库根运行 scripts/sync.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGETS=(
  "$HOME/.codex/skills/donghe-construction-team"
  "$HOME/.cursor/skills-cursor/donghe-construction-team"
)

if [[ ! -f "$REPO_ROOT/SKILL.md" ]]; then
  echo "错误：$REPO_ROOT 下找不到 SKILL.md，不是施工队仓库根" >&2
  exit 1
fi

if [[ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]]; then
  echo "警告：仓库有未提交改动。SSOT 原则要求先 commit 再同步。" >&2
  read -r -p "仍要继续同步？[y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || exit 1
fi

for target in "${TARGETS[@]}"; do
  mkdir -p "$target"
  # docs/ 是本仓库自身的工地档案（任务卡 / 施工日志），不属于 skill 内容，不下发
  rsync -a --delete --exclude '.git' --exclude 'docs' "$REPO_ROOT/" "$target/"
  echo "已同步 → $target"
done

echo "完成。两处安装位置均为仓库同步产物，请勿直接修改。"
