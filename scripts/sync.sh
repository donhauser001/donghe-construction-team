#!/usr/bin/env bash
# Distribute only committed runtime files; retain old installations outside discovery.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -z "$(git -C "$REPO_ROOT" status --porcelain)" ]] || { echo 'Commit changes before distribution.' >&2; exit 1; }
BACKUP_ROOT="${DONGHE_BACKUP_ROOT:-$HOME/.donghe/backups}/$(date +%Y%m%d-%H%M%S)-$$"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
for item in SKILL.md CHANGELOG.md LICENSE README.md README.en.md agents references playbooks templates scripts assets; do
  [[ ! -e "$REPO_ROOT/$item" ]] || cp -R "$REPO_ROOT/$item" "$STAGE/"
done
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +
if (( $# )); then TARGETS=("$@"); else TARGETS=("$HOME/.codex/skills/donghe-construction-team" "$HOME/.cursor/skills/donghe-construction-team"); fi
mkdir -p "$BACKUP_ROOT"
git -C "$REPO_ROOT" rev-parse HEAD > "$BACKUP_ROOT/source-commit.txt"
i=0
for target in "${TARGETS[@]}"; do
  [[ "$target" = /*/donghe-construction-team && ! -L "$target" ]] || { echo "Invalid target: $target" >&2; exit 1; }
  i=$((i+1))
  printf '%s\n' "$target" > "$BACKUP_ROOT/$i.target"
  if [[ -e "$target" ]]; then cp -a "$target" "$BACKUP_ROOT/$i.previous"; fi
  mkdir -p "$target"
  rsync -a --delete "$STAGE/" "$target/"
  diff -qr "$STAGE" "$target"
  python3 "$target/scripts/donghe.py" --help >/dev/null
  echo "Installed: $target"
done
echo "Backup and source commit: $BACKUP_ROOT"
