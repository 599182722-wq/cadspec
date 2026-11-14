# -*- coding: utf-8 -*-
"""命令行入口。

  cadspec check  <dxf> [--scale 100] [--format text|md|json] [--only R-xxx]
  cadspec kb     list [--grep 关键字]
  cadspec kb     show <clause_id>
  cadspec kb     verify
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

__version__ = "0.1.0"


def _fix_stdout() -> None:
    from .console import fix_console

    fix_console()


def cmd_check(args: argparse.Namespace) -> int:
    from . import report
    from .kb import load_clauses, load_rules, validate_kb
    from .review import build_context
    from .rules import run_rules

    path = Path(args.dxf)
    if not path.exists():
        print(f"找不到文件：{path}", file=sys.stderr)
        return 2

    clauses = load_clauses()
    rules = load_rules()
    problems = validate_kb(clauses, rules)
    if problems:
        print("知识库自检未通过：", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)

    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        rules = [r for r in rules if r.rule_id in wanted]

    try:
        ctx = build_context(path, scale=args.scale, level=args.level)
    except Exception as exc:
        print(f"读取 DXF 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    results = run_rules(ctx, rules)

    if args.format == "json":
        text = report.render_json(ctx, results)
    elif args.format == "md":
        text = report.render_markdown(ctx, results, clauses)
    else:
        text = report.render_text(ctx, results, clauses, version=__version__)

    if args.output:
        # 直接写文件，绕过 shell 管道：Windows PowerShell 在管道里会给 UTF-8
        # 输出加 BOM，下游的 json.load 会直接报 "Unexpected UTF-8 BOM"。
        # agent 调用时应该用 -o，而不是 `cadspec ... | python`。
        Path(args.output).write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        print(f"已写入 {args.output}", file=sys.stderr)
    else:
        print(text)

    n_err = sum(1 for _r, fs in results for f in fs if f.severity.value == "error")
    return 1 if (n_err and args.strict) else 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """体检：这张图能被自动审到什么程度，要改哪几处。"""
    from . import doctor

    path = Path(args.dxf)
    if not path.exists():
        print(f"找不到文件：{path}", file=sys.stderr)
        return 2
    try:
        rep = doctor.diagnose(path)
    except Exception as exc:
        print(f"读取 DXF 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    text = doctor.render_json(rep) if args.format == "json" else doctor.render_text(rep, version=__version__)
    if args.output:
        Path(args.output).write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        print(f"已写入 {args.output}", file=sys.stderr)
    else:
        print(text)
    return 0


def _render_table(t: dict) -> list[str]:
    """把一张附表排成等宽文本。中文按两格算。"""
    def w(s: str) -> int:
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)

    header = [str(h) for h in t.get("header", [])]
    rows = [[str(c) for c in r] for r in t.get("rows", [])]
    if not rows:
        return []
    ncol = len(header) or len(rows[0])
    widths = [0] * ncol
    for r in [header] + rows:
        for i in range(min(ncol, len(r))):
            widths[i] = max(widths[i], w(r[i]))

    def line(cells: list[str]) -> str:
        out = []
        for i in range(ncol):
            cell = cells[i] if i < len(cells) else ""
            out.append(cell + " " * (widths[i] - w(cell)))
        return "  " + "  ".join(out).rstrip()

    out = [line(header), "  " + "  ".join("-" * x for x in widths)]
    out += [line(r) for r in rows]
    return out


def cmd_kb(args: argparse.Namespace) -> int:
    from .kb import load_clauses, load_rules, load_tables, validate_kb

    clauses = load_clauses()

    if args.action == "verify":
        rules = load_rules()
        tables = load_tables()
        problems = validate_kb(clauses, rules, tables)
        if not problems:
            print(f"知识库自检通过：{len(clauses)} 条条文，{len(rules)} 条规则，"
                  f"{len(tables)} 张附表。")
            return 0
        for p in problems:
            print("  ✗ " + p)
        return 1

    if args.action == "coverage":
        rules = load_rules()
        tally = {"full": 0, "partial": 0, "none": 0, "unset": 0}
        by_std: dict[str, dict[str, int]] = {}
        for c in clauses.values():
            lv = (c.machine or {}).get("checkable")
            key = lv if lv in ("full", "partial", "none") else "unset"
            tally[key] += 1
            d = by_std.setdefault(c.standard, {
                "total": 0, "full": 0, "partial": 0, "none": 0,
                "unset": 0, "rules": 0,
            })
            d["total"] += 1
            d[key] += 1
        for r in rules:
            if r.cite and r.cite in clauses:
                by_std[clauses[r.cite].standard]["rules"] += 1

        total = len(clauses)
        declared = tally["full"] + tally["partial"] + tally["none"]
        print("条文可判定性覆盖")
        print(f"  条文总数        {total}")
        print(f"  已声明 checkable {declared}"
              f"（{declared / total * 100:.0f}%）" if total else "")
        print(f"    full      {tally['full']:>4}   信息齐全，可自动判定")
        print(f"    partial   {tally['partial']:>4}   可判但不完整，需要外部输入或会误报")
        print(f"    none      {tally['none']:>4}   明确判不了，只供检索")
        print(f"    未声明    {tally['unset']:>4}   默认按 none 处理")
        print(f"  规则数          {len(rules)}")
        cited = {r.cite for r in rules if r.cite}
        print(f"  被规则引用的条文 {len(cited)}")
        print()
        head = (f"  {'标准':<16}{'条文':>5}{'full':>6}{'partial':>9}"
                f"{'none':>6}{'未声明':>8}{'规则':>6}")
        print(head)
        print("  " + "-" * (len(head) - 2))
        for std in sorted(by_std):
            d = by_std[std]
            print(f"  {std:<16}{d['total']:>5}{d['full']:>6}{d['partial']:>9}"
                  f"{d['none']:>6}{d['unset']:>8}{d['rules']:>6}")
        return 0

    if args.action == "table":
        tables = load_tables()
        if args.clause_id:
            tables = [t for t in tables
                      if args.clause_id in (t.get("table_id") or "")
                      or args.clause_id in (t.get("clause") or "")]
        for t in tables:
            print(f'\n{t.get("standard")}  表 {t.get("table_id")}  {t.get("title", "")}')
            print(f'  挂靠条文 {t.get("clause", "—")}   {len(t.get("rows") or [])} 行')
            for ln in _render_table(t):
                print(ln)
            if t.get("note"):
                print("\n  注：" + " ".join(str(t["note"]).split()))
        print(f"\n共 {len(tables)} 张")
        return 0

    if args.action == "list":
        rows = sorted(clauses.values(), key=lambda c: c.clause_id)
        if args.grep:
            kw = args.grep.lower()
            rows = [c for c in rows
                    if kw in c.clause_id.lower()
                    or kw in (c.verbatim or "").lower()
                    or kw in (c.interpret or "").lower()
                    or any(kw in str(k).lower() for k in (c.keywords or []))]
        for c in rows:
            sev = c.severity_hint.value
            mark = {"error": "!", "warning": "~", "info": "-"}[sev]
            print(f"{mark} {c.clause_id:<34} {c.standard_name} {c.chapter}")
            print(f"    {c.interpret.splitlines()[0][:96] if c.interpret else ''}")
        print(f"\n共 {len(rows)} 条")
        return 0

    if args.action == "show":
        clause = clauses.get(args.clause_id)
        if clause is None:
            print(f"知识库中没有 {args.clause_id}", file=sys.stderr)
            print("可用条号：", ", ".join(sorted(clauses)))
            return 1
        print(f"{clause.clause_id}")
        print(f"  {clause.standard}《{clause.standard_name}》 {clause.chapter}")
        print(f"  状态 {clause.status.value}  用词强度 {clause.severity_hint.value}")
        print("\n【条文原文】")
        for line in clause.verbatim.splitlines():
            print("  " + line)
        print("\n【解读】")
        for line in clause.interpret.splitlines():
            print("  " + line)
        if clause.machine:
            print("\n【机器可读约束】")
            for k, v in clause.machine.items():
                print(f"  {k}: {v}")
        linked = [t for t in load_tables() if t.get("clause") == clause.clause_id]
        for t in linked:
            print(f'\n【附表 {t.get("table_id")}】{t.get("title", "")}')
            for line in _render_table(t):
                print(line)
            if t.get("note"):
                print("\n  注：" + " ".join(str(t["note"]).split()))
        if clause.source_url:
            print(f"\n来源 {clause.source_url}")
        if clause.source_note:
            print(f"注：{clause.source_note}")
        return 0

    return 2


def main(argv: list[str] | None = None) -> int:
    _fix_stdout()
    parser = argparse.ArgumentParser(
        prog="cadspec",
        description="中国建筑制图规范知识库 + DXF 合规审查",
    )
    parser.add_argument("--version", action="version", version=f"cadspec {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="审查一张 DXF")
    p_check.add_argument("dxf", help="DXF 文件路径")
    p_check.add_argument("--scale", type=int, default=100,
                         help="出图比例分母，1:100 就传 100（默认 100）")
    p_check.add_argument("--format", choices=["text", "md", "json"], default="text")
    p_check.add_argument("--only", default="", help="只跑指定规则，逗号分隔，如 R-TEXT-001,R-LW-001")
    p_check.add_argument("-o", "--output", default="",
                         help="把报告写入文件（UTF-8 无 BOM）；agent/CI 消费 JSON 时用它，"
                              "不要走 PowerShell 管道")
    p_check.add_argument("--level", choices=["basic", "strict"], default="basic",
                         help="basic 只查图纸表达（默认）；strict 额外做几何识别，"
                              "启用房量/短边等尺寸类规则，要求墙线用 MLINE 且在「外墙」「内墙」图层")
    p_check.add_argument("--strict", action="store_true", help="有 error 时返回非零退出码")
    p_check.set_defaults(func=cmd_check)

    p_doc = sub.add_parser("doctor", help="体检：这张图能被自动审到什么程度，要改哪几处")
    p_doc.add_argument("dxf", help="DXF 文件路径")
    p_doc.add_argument("--format", choices=["text", "json"], default="text")
    p_doc.add_argument("-o", "--output", default="",
                       help="把结果写入文件（UTF-8 无 BOM）")
    p_doc.set_defaults(func=cmd_doctor)

    p_kb = sub.add_parser("kb", help="查询/校验知识库")
    p_kb.add_argument("action", choices=["list", "show", "verify", "table", "coverage"])
    p_kb.add_argument("clause_id", nargs="?", help="show 时的条号，或 table 时的表号，如 GBT50001-2017/8.0.6")
    p_kb.add_argument("--grep", default="", help="list 时按关键字过滤")
    p_kb.set_defaults(func=cmd_kb)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

