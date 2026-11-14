"""体检：条文切分的常见毛病。

机械切分最怕两类错误——多切（把跨行引用当成新条文）和漏切（整条并进上一条）。
两类都会让 knowledge base 静默带上脏数据，所以单独跑一遍：

    python scripts/check_clauses.py data/kb/gb55037-2022.yaml

退出码非 0 表示发现问题。
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

from cadspec.console import fix_console

ID_RE = re.compile(r"^(?P<std>[^/]+)/(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-\d+)?$")

# 条文正文不该以这些字开头；出现就说明大概率是把上一句的续行切成了新条文
SUSPECT_STARTS = ("条", "款", "。", "，", "、", "；", "：")


def main() -> int:
    fix_console()
    ap = argparse.ArgumentParser(description="检查条文 YAML 的切分质量")
    ap.add_argument("yaml_path", type=Path)
    args = ap.parse_args()

    doc = yaml.safe_load(args.yaml_path.read_text(encoding="utf-8"))
    clauses = doc["clauses"]
    problems: list[str] = []

    ids = [c["clause_id"] for c in clauses]

    dup = [cid for cid, n in Counter(ids).items() if n > 1]
    for cid in dup:
        problems.append(f"重复 id：{cid}（出现 {Counter(ids)[cid]} 次，后一条会覆盖前一条）")

    for clause in clauses:
        cid = clause["clause_id"]
        text = (clause.get("verbatim") or "").strip()

        if text.startswith(SUSPECT_STARTS):
            problems.append(f"{cid} 的正文以「{text[0]}」开头，疑似上一句的续行被切成了新条文：{text[:40]}")

        # 阈值取 8 而不是 12：有些条文本身就短（如「门不应跨越变形缝设置。」11 字），
        # 定太高会把完整条文当截断。
        if len(text) < 8:
            problems.append(f"{cid} 正文只有 {len(text)} 字，疑似截断：{text}")

        if not clause.get("chapter"):
            problems.append(f"{cid} 没有 chapter（章标题没匹配上）")

    # 章节内编号应当连续：2.2.8 之后是 2.2.9，中间冒出 7.1.8 就是别处引用被误切
    prev = None
    for clause in clauses:
        cid = clause["clause_id"]
        m = ID_RE.match(cid)
        if not m:
            problems.append(f"{cid} 编号格式异常")
            continue
        cur = (int(m["major"]), int(m["minor"]), int(m["patch"]))
        if prev is not None:
            pm, pmi, pp = prev
            same_sec = (cur[0], cur[1]) == (pm, pmi)
            if same_sec and cur[2] != pp + 1:
                problems.append(f"{cid} 在同一节内跳号：上一节是 {pm}.{pmi}.{pp}，本条是 {cid}")
            if (cur[0], cur[1]) < (pm, pmi):
                problems.append(f"{cid} 出现章节回退：上一节是 {pm}.{pmi}，本条是 {cur[0]}.{cur[1]}")
        prev = cur

    if problems:
        print(f"发现 {len(problems)} 处问题（共 {len(clauses)} 条）：")
        for line in problems:
            print("  -", line)
        return 1

    print(f"体检通过：{len(clauses)} 条，无重复 id、无跳号、无截断。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
