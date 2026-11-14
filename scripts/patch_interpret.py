"""把人工补写的 interpret 合并进机械生成的条文 YAML。

机械切分脚本只保证 verbatim 正确；interpret 需要读懂条文后逐条写，
所以单独走这个补丁通道，避免重跑 ingest 时把人工内容覆盖掉。

用法：
    python scripts/patch_interpret.py data/kb/gb55037-2022.yaml patches/ch1.json

补丁文件是 JSON 对象：{"条款 id": "解读文字", ...}。id 必须已存在于 YAML，
拼错会整体拒绝并退出，不会静默写入。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

# 写回时的字段顺序。interpret 紧跟 verbatim，读起来是"原文 / 解读"两段。
FIELD_ORDER = [
    "clause_id",
    "chapter",
    "section",
    "verbatim",
    "interpret",
    "severity_hint",
    "machine",
    "keywords",
    "status",
    "source_note",
]


class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _str_representer(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_representer)


def reorder(clause: dict) -> dict:
    """按 FIELD_ORDER 重排字段，丢掉空值，未知字段排在最后。"""
    out: dict = {}
    for key in FIELD_ORDER:
        if key in clause and clause[key] not in (None, "", [], {}):
            out[key] = clause[key]
    for key, value in clause.items():
        if key not in out and value not in (None, "", [], {}):
            out[key] = value
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="合并 interpret 补丁")
    ap.add_argument("yaml_path", type=Path)
    ap.add_argument("patch_json", type=Path)
    args = ap.parse_args()

    doc = yaml.safe_load(args.yaml_path.read_text(encoding="utf-8"))
    patches = json.loads(args.patch_json.read_text(encoding="utf-8"))
    if not isinstance(patches, dict):
        print("补丁文件必须是一个 JSON 对象：{条款 id: 解读}")
        return 1

    known = {c["clause_id"] for c in doc["clauses"]}
    unknown = sorted(set(patches) - known)
    if unknown:
        print(f"补丁里有 {len(unknown)} 个 id 在 YAML 中不存在，拒绝写入：")
        for cid in unknown[:15]:
            print("  ", cid)
        return 1

    applied = 0
    for clause in doc["clauses"]:
        text = patches.get(clause["clause_id"])
        if not text or not text.strip():
            continue
        text = text.strip()
        old = (clause.get("interpret") or "").strip()
        if old and old != text:
            print(f"  覆盖已有解读：{clause['clause_id']}")
        clause["interpret"] = text
        applied += 1

    doc["clauses"] = [reorder(c) for c in doc["clauses"]]
    body = yaml.dump(
        doc,
        Dumper=_Dumper,
        allow_unicode=True,
        sort_keys=False,
        width=1000,
        default_flow_style=False,
    )
    args.yaml_path.write_text(body, encoding="utf-8")
    print(f"已写入 {applied} 条解读 → {args.yaml_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
