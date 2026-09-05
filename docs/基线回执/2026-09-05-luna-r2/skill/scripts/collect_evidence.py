#!/usr/bin/env python3
"""Generate a compact Markdown evidence block for Donghe task cards."""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path


def run(command: str, timeout: int) -> tuple[int, str, str]:
    proc = subprocess.run(
        command,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def safe_run(command: str, timeout: int = 20) -> str:
    try:
        code, out, err = run(command, timeout)
    except Exception as exc:  # noqa: BLE001 - evidence should record environment failure.
        return f"$ {command}\nERROR: {exc}"
    body = out or err or "(no output)"
    return f"$ {command}\nexit={code}\n{body}"


def in_git_repo() -> bool:
    try:
        code, out, _ = run("git rev-parse --is-inside-work-tree", 10)
    except Exception:
        return False
    return code == 0 and out.strip() == "true"


def trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... trimmed {len(text) - limit} chars ..."


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect git and command evidence as Markdown.")
    parser.add_argument("--cmd", action="append", default=[], help='Command in "label::command" form.')
    parser.add_argument("--out", help="Output Markdown path. Prints to stdout when omitted.")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout per command in seconds.")
    parser.add_argument("--max-chars", type=int, default=6000, help="Max chars kept per command output.")
    args = parser.parse_args()

    started = dt.datetime.now().isoformat(timespec="seconds")
    lines: list[str] = []
    lines.append("# 机器证据")
    lines.append("")
    lines.append(f"- 生成时间：{started}")
    git_ok = in_git_repo()
    if git_ok:
        lines.append(f"- commit：`{safe_run('git rev-parse HEAD').splitlines()[-1]}`")
    else:
        lines.append("- commit：非 Git 仓库")
    lines.append("")
    lines.append("## Git 状态")
    lines.append("")
    lines.append("```text")
    if git_ok:
        lines.append(safe_run("git status --short"))
        lines.append("")
        lines.append(safe_run("git diff --stat"))
    else:
        lines.append("非 Git 仓库，跳过 git status / diff stat。")
    lines.append("```")

    if args.cmd:
        lines.append("")
        lines.append("## 验证命令")
    for raw in args.cmd:
        if "::" in raw:
            label, command = raw.split("::", 1)
        else:
            label, command = raw, raw
        start = dt.datetime.now().isoformat(timespec="seconds")
        lines.append("")
        lines.append(f"### {label}")
        lines.append("")
        lines.append(f"- 开始：{start}")
        try:
            code, out, err = run(command, args.timeout)
            output = "\n".join(part for part in [out, err] if part)
        except subprocess.TimeoutExpired as exc:
            code = 124
            output = f"TIMEOUT after {args.timeout}s\n{exc}"
        except Exception as exc:  # noqa: BLE001
            code = 1
            output = f"ERROR: {exc}"
        end = dt.datetime.now().isoformat(timespec="seconds")
        lines.append(f"- 结束：{end}")
        lines.append(f"- 退出码：{code}")
        lines.append("")
        lines.append("```text")
        lines.append(f"$ {command}")
        lines.append(trim(output or "(no output)", args.max_chars))
        lines.append("```")

    rendered = "\n".join(lines).rstrip() + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        print(out)
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
