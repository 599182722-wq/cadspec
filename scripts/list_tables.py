"""盘点条文里提到的表格：哪些表格被引用、是否有可靠文本来源。

用法：python scripts/list_tables.py
"""

from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TABLE_RE = re.compile(r"表\s?(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    for f in sorted(glob.glob(str(ROOT / "data/kb/*.yaml"))):
        d = yaml.safe_load(open(f, encoding="utf-8"))
        cs = d.get("clauses", [])
        hits: dict[str, list[str]] = {}
        for c in cs:
            for t in TABLE_RE.findall(c.get("verbatim") or ""):
                hits.setdefault(t, []).append(c["clause_id"].split("/")[-1])
        if not hits:
            continue
        print(f"\n=== {d.get('standard')} {d.get('standard_name', '')} ===")
        for t in sorted(hits, key=lambda x: [int(p) for p in x.split(".")]):
            refs = hits[t]
            print(f"  表 {t}  被 {len(refs)} 条引用: {', '.join(refs[:6])}"
                  f"{' ...' if len(refs) > 6 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
