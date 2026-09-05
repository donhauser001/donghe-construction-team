**English** | [中文](README.md)

# Donghe Construction Team (东合施工队)

An **engineering discipline skill** for AI coding agents. As of v0.6.1, `SKILL.md` is a one-page router around three non-negotiables:

- **Machine evidence**: claiming "done" requires real runtime evidence (generated via `scripts/collect_evidence.py`); "build passes" does not count as feature completion.
- **Scope discipline**: declare the change scope before touching anything, write only within it, never drive-by-fix unrelated code.
- **Honest reporting**: banned filler words ("should be fine", "quick temporary fix", etc.); blind spots and unverified items stated explicitly.

Tasks are graded S/M/L: the agent handles S/M work itself with zero process overhead; only L-level work (cross-layer / schema changes / money-permission-safety / genuinely parallel) spawns task cards and cheap-tier parallel workers.

Current version is in the `SKILL.md` title and the latest `CHANGELOG.md` entry.

> **Note on language:** The skill is written in Chinese by design. Its consumers are LLM agents—mainstream models read Chinese rules without difficulty—and a single-language source avoids rule divergence. This English README is maintained in sync with the Chinese version.

## Directory layout

| Path | Contents |
|---|---|
| `SKILL.md` | Charter + on-demand routing |
| `references/models.md` | Task model tiers (host list wins; never inherit) |
| `references/unattended.md` | Unattended-run contract |
| `templates/任务卡模板.md` | Slim task card for L-level dispatch (≤ 40 lines) |
| `agents/openai.yaml` | Codex agent entry |
| `scripts/collect_evidence.py` | Machine evidence collection (non-zero if a command fails) |
| `scripts/sync.sh` | One-way sync from repo → local install location |
| `evals/evals.json` | Author eval prompts (not installed) |
| `CHANGELOG.md` | Version history |

## Installation

This repo is the **sole source of truth (SSOT)**. Clone and sync to your agent's skill directory:

```bash
git clone https://github.com/donhauser001/donghe-construction-team.git
cd donghe-construction-team
scripts/sync.sh
```

`sync.sh` defaults to:

- Codex: `~/.codex/skills/donghe-construction-team/`
- Cursor: `~/.cursor/skills/donghe-construction-team/` (personal skills; never `~/.cursor/skills-cursor/`)

For other locations, edit the `TARGETS` array in the script.

## Modification workflow

1. Edit only in this repo—**never edit files directly in the install location**.
2. Keep the version in sync across four places: `SKILL.md` frontmatter description (when relevant), `SKILL.md` title and metadata, `agents/openai.yaml`, and a new `CHANGELOG.md` entry.
3. Keep the single-page principle: before adding a rule, consider which old one to remove; SKILL.md exceeding ~120 lines is a bloat signal.
4. After commit, run `scripts/sync.sh` to push to local install locations.

## License

[MIT](LICENSE)

## v0.6.2 integration

Includes the project-scoped completion CLI and evolution UI. Requires Python 3.9+ on macOS/Linux. Shared fingerprints and bounded receipt summaries preserve legacy evidence. Archive automation, graph installation and bundled runtimes are not yet implemented. Distribution backs up previous installations under `~/.donghe/backups/` and excludes repository evidence and evaluations.
