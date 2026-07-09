**English** | [中文](README.md)

# Donghe Construction Team (东合施工队)

An **engineering closed-loop skill** for AI coding agents: agents take on non-trivial coding work as a "construction crew," not a lone implementer. The foreman (strong parent agent) handles site survey, task breakdown, dispatch, audit, and acceptance; workers (cheap-tier sub-agents) build from task cards; completion is judged by real runtime machine evidence throughout.

Current version is in the `SKILL.md` title and the latest `CHANGELOG.md` entry.

> **Note on language:** Playbooks and templates are written in Chinese by design. The consumers of this skill are LLM agents—mainstream models read Chinese rules without difficulty—and a single-language source avoids rule divergence. This English README is maintained in sync with the Chinese version.

## Core mechanisms

- **S/M/L change radius grading**: Radius determines process rigor; you may escalate but never downgrade.
- **On-disk task cards + triple binding**: M/L work must land slim task cards in `docs/任务卡/`, with mandatory "dispatch to / model / match rationale" fields.
- **Parallel orchestration**: Default ≤3 workers; six-slot formation (clerk / patrol / audit / 3 workers); with user authorization, burst waves up to 8 workers.
- **Workers don't idle / async audit**: Audit is decoupled from construction; small issues go back to the worker, big issues become new cards.
- **Machine evidence**: Completion claims require real runtime evidence; prefer generating via `scripts/collect_evidence.py`.
- **Unattended auto-build**: Time contract, no empty runs, decisions don't stall, technical-debt ledger, blueprint auto-delegation, temporary-branch push isolation.
- **Code graph assist**: When connected to a codebase-memory-mcp-style knowledge graph, use it as a structure-discovery accelerator (results must be confirmed by re-reading source).

## Directory layout

| Path | Contents |
|---|---|
| `SKILL.md` | Charter (eight iron rules + routing table + governance, ≤ 120 lines) |
| `playbooks/` | Eight scenario SOPs (intake flow, task cards & workers, dispatch & parallelism, verification & audit, reporting & docs, project onboarding & site survey, multi-crew coordination, unattended mode) |
| `templates/` | Task card, site survey report, build log, technical-debt ledger, unattended kit, and other templates |
| `agents/openai.yaml` | Codex agent entry (minimal prompt + reroute triggers) |
| `scripts/collect_evidence.py` | Machine evidence collection (git status + validation command output → Markdown) |
| `scripts/check_card.py` | Dispatch gate: task card required fields / whitelist / banned-word checks—no dispatch if it fails |
| `scripts/sync.sh` | One-way sync from repo → local install location |
| `scripts/lint.sh` | Internal link liveness + version consistency + line-budget checks (prevents rule bloat) |
| `CHANGELOG.md` | Version history (body is not organized by version; look here for all history) |

## Installation

This repo is the **sole source of truth (SSOT)**. Clone and sync to your agent's skill directory:

```bash
git clone https://github.com/donhauser001/donghe-construction-team.git
cd donghe-construction-team
scripts/sync.sh
```

`sync.sh` defaults to:

- Codex: `~/.codex/skills/donghe-construction-team/`
- Cursor: `~/.cursor/skills-cursor/donghe-construction-team/`

For other locations, edit the `TARGETS` array in the script.

## Modification workflow

1. Edit only in this repo—**never edit files directly in the install location**.
2. Version must stay in sync across four places: `SKILL.md` title, `SKILL.md` metadata, `agents/openai.yaml`, and a new `CHANGELOG.md` entry.
3. **One rule, one home**; new rules must state in CHANGELOG what was "removed / merged"; add-only without removal is a governance defect.
4. Run `scripts/lint.sh` and pass (includes line budgets: SKILL ≤ 120 lines, single playbook ≤ 220 lines, total ≤ 1600 lines).
5. After commit, run `scripts/sync.sh` to push to local install locations.

## License

[MIT](LICENSE)
