#!/usr/bin/env python3
"""派工闸脚本：派 Task 前校验任务卡。全部通过退出码 0，否则 1。

用法：python scripts/check_card.py <任务卡路径> [<任务卡路径>...]

检查项：
  1. 三元绑定字段（派给 / 调用模型 / 身份-模型匹配理由）已填且非占位
  2. parent_focus 已填且非占位
  3. 必填段齐全（当前状态 / 改动范围 / 不变量 / 施工要点 / 证据命令 / 验收点 / AI 工具同步）
  4. 白名单至少一条路径；验收点至少一个 checkbox
  5. 施工要点 / 验收点无禁词
"""
import re
import sys
from pathlib import Path

PLACEHOLDERS = re.compile(r"(TODO|xxx|XXX|待定|待填|留空|^\s*$|^—$|^-$)")
# 模板里的枚举提示（含 / 分隔的多个候选）也算未填
TEMPLATE_HINT = re.compile(r"工人#\d\s*/\s*工人#\d|具体\s*slug|一句话")

REQUIRED_SECTIONS = [
    "当前状态", "改动范围", "不变量", "施工要点", "证据命令", "验收点", "AI 工具同步",
]

FORBIDDEN_WORDS = [
    "暂时", "临时", "先这样", "TODO 后续优化", "简单起见", "后面再说",
    "可能", "大概", "修一下", "调一下", "优化下",
]

TRIPLE_FIELDS = ["派给", "调用模型", "身份-模型匹配理由"]


def field_value(text: str, name: str) -> str:
    m = re.search(rf"\*?\*?{re.escape(name)}\*?\*?[^：:]*[：:]\s*(.+)", text)
    return m.group(1).strip() if m else ""


def section_body(text: str, name: str) -> str:
    m = re.search(rf"^#+\s*{re.escape(name)}.*?$(.*?)(?=^#+\s|\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def check(path: Path) -> list[str]:
    problems: list[str] = []
    if not path.is_file():
        return [f"文件不存在：{path}"]
    text = path.read_text(encoding="utf-8")

    for f in TRIPLE_FIELDS:
        v = field_value(text, f)
        if not v or PLACEHOLDERS.search(v) or TEMPLATE_HINT.search(v):
            problems.append(f"三元字段「{f}」缺失或未填具体值：{v or '(空)'}")

    pf = field_value(text, "parent_focus") or field_value(text, "`parent_focus`")
    if not pf or PLACEHOLDERS.search(pf) or "focus-1`" in pf:
        problems.append(f"parent_focus 缺失或为占位值：{pf or '(空)'}")

    for sec in REQUIRED_SECTIONS:
        if not re.search(rf"^#+\s*{re.escape(sec)}", text, re.M):
            problems.append(f"缺必填段：## {sec}")

    scope = section_body(text, "改动范围")
    if scope and not re.search(r"^\s*[-*]\s*`?[\w./]", scope, re.M):
        problems.append("改动范围（白名单）没有任何具体路径条目")

    accept = section_body(text, "验收点")
    if accept and not re.search(r"- \[[ x]\]", accept):
        problems.append("验收点没有任何 checkbox")

    for sec in ["施工要点", "验收点"]:
        body = section_body(text, sec)
        for w in FORBIDDEN_WORDS:
            if w in body:
                problems.append(f"「{sec}」含禁词：{w}")

    return problems


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    failed = 0
    for arg in sys.argv[1:]:
        path = Path(arg)
        problems = check(path)
        if problems:
            failed += 1
            print(f"✗ {path}")
            for p in problems:
                print(f"    - {p}")
        else:
            print(f"✓ {path} 派工闸通过")
    if failed:
        print(f"\n{failed} 张卡未过派工闸：补全后再派工。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
