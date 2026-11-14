# -*- coding: utf-8 -*-
"""知识库加载：把 data/kb/*.yaml 读成 Clause，把 data/rules/*.yaml 读成 Rule。"""
from __future__ import annotations

from pathlib import Path

import yaml

from .model import Clause, Rule, Severity, Status

def _resolve_data() -> Path:
    """找到 data 目录。

    开发时（`pip install -e .`）数据在仓库根目录；正常安装时它被打包进
    `cadspec/_data/`（见 pyproject 的 force-include）。两种都要能找到，
    否则用户装完 `cadspec kb list` 会返回「共 0 条」。
    """
    pkg = Path(__file__).resolve().parent
    for cand in (pkg / "_data", Path(__file__).resolve().parents[2] / "data"):
        if (cand / "kb").is_dir():
            return cand
    raise FileNotFoundError(
        "找不到 data 目录。开发时请在仓库根目录运行，安装版请重装以带上数据文件。"
    )


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = _resolve_data()
KB_DIR = DATA_DIR / "kb"
RULES_DIR = DATA_DIR / "rules"
TABLES_DIR = DATA_DIR / "tables"


def _read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_clauses(kb_dir: Path | None = None) -> dict[str, Clause]:
    """返回 {clause_id: Clause}。文件头的 standard 作为缺省值下发到每条。"""
    kb_dir = kb_dir or KB_DIR
    out: dict[str, Clause] = {}
    for path in sorted(kb_dir.glob("*.yaml")):
        doc = _read_yaml(path)
        default_standard = doc.get("standard", "")
        default_name = doc.get("standard_name", "")
        default_status = doc.get("status", "effective")
        default_url = doc.get("source_url", "")
        default_note = doc.get("source_note", "")
        default_from = doc.get("effective_from", "")

        for raw in doc.get("clauses", []):
            known = {
                "clause_id", "standard", "standard_name", "chapter", "verbatim",
                "interpret", "keywords", "severity_hint", "status",
                "superseded_by", "effective_from", "machine", "source_url",
                "source_note",
            }
            extra = {k: v for k, v in raw.items() if k not in known}
            clause = Clause(
                clause_id=raw["clause_id"],
                standard=raw.get("standard", default_standard),
                standard_name=raw.get("standard_name", default_name),
                chapter=raw.get("chapter", ""),
                verbatim=(raw.get("verbatim") or "").strip(),
                interpret=(raw.get("interpret") or "").strip(),
                keywords=raw.get("keywords") or [],
                severity_hint=Severity(raw.get("severity_hint", "warning")),
                status=Status(raw.get("status", default_status)),
                superseded_by=raw.get("superseded_by") or [],
                effective_from=raw.get("effective_from", default_from),
                machine=raw.get("machine"),
                source_url=raw.get("source_url", default_url),
                source_note=raw.get("source_note", default_note),
            )
            # 未建模的字段（如 GB55038 的 supersedes 清单）挂到 clause 上备用
            for key, val in extra.items():
                setattr(clause, key, val)
            out[clause.clause_id] = clause
    return out


def load_rules(rules_dir: Path | None = None) -> list[Rule]:
    rules_dir = rules_dir or RULES_DIR
    out: list[Rule] = []
    for path in sorted(rules_dir.glob("*.yaml")):
        doc = _read_yaml(path)
        for raw in doc.get("rules", []):
            out.append(
                Rule(
                    rule_id=raw["rule_id"],
                    title=raw["title"],
                    severity=Severity(raw.get("severity", "warning")),
                    check=raw["check"],
                    params=raw.get("params") or {},
                    cite=raw.get("cite"),
                    rationale=(raw.get("rationale") or "").strip(),
                    note=(raw.get("note") or "").strip(),
                )
            )
    return out


def load_tables(tables_dir: Path | None = None) -> list[dict]:
    """规范附表。条文正文只写「应符合表 X 的规定」，数值在这里。

    每张表：table_id / standard / title / clause / header / rows / note。
    """
    tables_dir = tables_dir or TABLES_DIR
    out: list[dict] = []
    for path in sorted(tables_dir.glob("*.yaml")):
        doc = _read_yaml(path)
        for raw in doc.get("tables", []):
            row = dict(raw)
            row.setdefault("standard", doc.get("standard", ""))
            row.setdefault("standard_name", doc.get("standard_name", ""))
            row.setdefault("source_note", doc.get("source_note", ""))
            out.append(row)
    return out


VALID_CHECKABLE = {"full", "partial", "none"}


def validate_kb(clauses: dict[str, Clause], rules: list[Rule],
                tables: list[dict] | None = None) -> list[str]:
    """自检：规则的 cite 必须真实存在；条文必须有 verbatim；表格必须挂在真实条文上；
    被规则引用的条文必须声明自己能判到什么程度。

    返回问题列表。
    """
    problems: list[str] = []
    for rule in rules:
        if rule.cite and rule.cite not in clauses:
            problems.append(f"{rule.rule_id}: cite 指向不存在的条文 {rule.cite}")
    for cid, clause in clauses.items():
        if not clause.verbatim:
            problems.append(f"{cid}: 缺少 verbatim 原文")
    for t in tables or []:
        tid = f'{t.get("standard", "")}/{t.get("table_id", "")}'
        if not t.get("rows"):
            problems.append(f"{tid}: 表格没有数据行")
        if t.get("header") and len(t["header"]) != len(t["rows"][0]):
            problems.append(
                f"{tid}: 表头 {len(t['header'])} 列，首行 {len(t['rows'][0])} 列，对不上"
            )
        clause_ref = t.get("clause")
        if clause_ref and clause_ref not in clauses:
            problems.append(f"{tid}: clause 指向不存在的条文 {clause_ref}")

    # machine.checkable 是规则与条文之间的契约：一条条文能被自动判定，
    # 必须在这里说清楚判到什么程度。否则规则是一厢情愿——它声称依据某条，
    # 而那条从未声明过自己可以判。
    cited = {r.cite for r in rules if r.cite}
    for cid in sorted(cited):
        clause = clauses.get(cid)
        if clause is None:
            continue  # 上面已经报过 cite 不存在
        machine = clause.machine or {}
        level = machine.get("checkable")
        if level is None:
            problems.append(
                f"{cid}: 被规则引用，但 machine 里没有 checkable"
                "（应为 full / partial / none 之一）"
            )
        elif level not in VALID_CHECKABLE:
            problems.append(
                f"{cid}: machine.checkable={level!r} 不是合法值，"
                f"只能是 {sorted(VALID_CHECKABLE)}"
            )
        elif level == "none":
            problems.append(
                f"{cid}: machine.checkable 标为 none，却被规则引用——"
                "要么把等级改成 partial/full，要么删掉引用它的规则"
            )
    return problems
