"""统计知识库现状：条文、解读覆盖率、规则、代码规模。

用法：python scripts/inventory.py
"""

from __future__ import annotations

import glob
import os
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    kb_files = sorted(glob.glob(str(ROOT / "data/kb/*.yaml")))
    total = 0
    print("【条文】")
    print(f"{'文件':24} {'条数':>5} {'有解读':>6} {'有约束':>6}  标准")
    for f in kb_files:
        d = yaml.safe_load(open(f, encoding="utf-8"))
        cs = d.get("clauses", [])
        total += len(cs)
        interp = sum(1 for c in cs if c.get("interpret"))
        mach = sum(1 for c in cs if c.get("machine"))
        print(
            f"{os.path.basename(f):24} {len(cs):5} {interp:6} {mach:6}  "
            f"{d.get('standard', '?')} {d.get('standard_name', '')}"
        )
    print(f"{'合计':24} {total:5}")

    print("\n【规则】")
    rule_total = 0
    for f in sorted(glob.glob(str(ROOT / "data/rules/*.yaml"))):
        d = yaml.safe_load(open(f, encoding="utf-8"))
        rs = d.get("rules", [])
        rule_total += len(rs)
        sev = Counter(r.get("severity", "?") for r in rs)
        print(f"{os.path.basename(f):24} {len(rs):5} 条  {dict(sev)}")
    print(f"{'合计':24} {rule_total:5}")

    print("\n【代码】")
    py = sorted(glob.glob(str(ROOT / "src/cadspec/*.py"))) + sorted(
        glob.glob(str(ROOT / "scripts/*.py"))
    )
    lines = 0
    for p in py:
        n = sum(1 for _ in open(p, encoding="utf-8"))
        lines += n
        print(f"  {os.path.relpath(p, ROOT):40} {n:5} 行")
    print(f"  {'合计':40} {lines:5} 行")

    print("\n【GB 55037 章分布】")
    p = ROOT / "data/kb/gb55037-2022.yaml"
    if p.exists():
        cs = yaml.safe_load(open(p, encoding="utf-8"))["clauses"]
        for ch, n in sorted(
            Counter(c["chapter"] for c in cs).items(),
            key=lambda kv: [int(x) for x in kv[0].split()[0].split(".")],
        ):
            print(f"  {ch:28} {n:3} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
