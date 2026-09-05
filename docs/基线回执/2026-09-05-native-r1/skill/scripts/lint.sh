#!/usr/bin/env bash
# 一致性校验：内链存活 + 版本号一致 + 行数预算（防规则再膨胀）。
# 用法：在仓库根运行 scripts/lint.sh；全部通过时退出码 0。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
errors=0

echo "== 1/3 内链存活检查（CHANGELOG 为历史记录，允许引用已删除文件，不检查）=="
refs=$(grep -rhoE '(playbooks/[^`）)[:space:]]+\.md|templates/[^`）)[:space:]]+\.md|scripts/[^`）)[:space:]]+\.(py|sh))' \
  SKILL.md README.md playbooks templates agents 2>/dev/null | sort -u)
while IFS= read -r ref; do
  [[ -z "$ref" ]] && continue
  if [[ ! -f "$ref" ]]; then
    echo "  ✗ 死链：$ref"
    errors=$((errors + 1))
  fi
done <<< "$refs"
[[ $errors -eq 0 ]] && echo "  ✓ 所有内部引用可解析"

echo "== 2/3 行数预算检查（SKILL ≤ 120 · 单 playbook ≤ 220 · playbooks 总量 ≤ 1600）=="
skill_lines=$(wc -l < SKILL.md | tr -d ' ')
if (( skill_lines > 120 )); then
  echo "  ✗ SKILL.md ${skill_lines} 行 > 120 行预算"
  errors=$((errors + 1))
else
  echo "  ✓ SKILL.md ${skill_lines}/120 行"
fi
total=0
for pb in playbooks/*.md; do
  n=$(wc -l < "$pb" | tr -d ' ')
  total=$((total + n))
  if (( n > 220 )); then
    echo "  ✗ $pb ${n} 行 > 220 行预算"
    errors=$((errors + 1))
  fi
done
if (( total > 1600 )); then
  echo "  ✗ playbooks 总量 ${total} 行 > 1600 行预算"
  errors=$((errors + 1))
else
  echo "  ✓ playbooks 总量 ${total}/1600 行"
fi

echo "== 3/3 版本号一致性检查 =="
v_title=$(grep -m1 -oE '^# 东合施工队 · v[0-9.]+' SKILL.md | grep -oE 'v[0-9.]+')
v_meta=$(grep -m1 -oE '\*\*当前版本\*\*：v[0-9.]+' SKILL.md | grep -oE 'v[0-9.]+')
v_yaml=$(grep -m1 -oE 'display_name: "东合施工队 v[0-9.]+' agents/openai.yaml | grep -oE 'v[0-9.]+')
v_log=$(grep -m1 -oE '^## v[0-9.]+' CHANGELOG.md | grep -oE 'v[0-9.]+')

echo "  SKILL 标题:        ${v_title:-缺失}"
echo "  SKILL 元信息:      ${v_meta:-缺失}"
echo "  openai.yaml:       ${v_yaml:-缺失}"
echo "  CHANGELOG 最新条目: ${v_log:-缺失}"

if [[ -z "$v_title" || "$v_title" != "$v_meta" || "$v_title" != "$v_yaml" || "$v_title" != "$v_log" ]]; then
  echo "  ✗ 版本号不一致"
  errors=$((errors + 1))
else
  echo "  ✓ 四处版本号一致：$v_title"
fi

if [[ $errors -gt 0 ]]; then
  echo "校验失败：$errors 项问题"
  exit 1
fi
echo "校验通过"
