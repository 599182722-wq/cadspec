# -*- coding: utf-8 -*-
"""报告渲染：终端文本 / Markdown / JSON。"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from .model import Clause, Finding, ReviewContext, Rule, Severity

USE_COLOR = os.environ.get("NO_COLOR") is None and sys.stdout.isatty()
C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "yellow": "\033[33m", "blue": "\033[34m",
    "green": "\033[32m", "cyan": "\033[36m",
}
LEVEL_COLOR = {"error": "red", "warning": "yellow", "info": "blue"}
LEVEL_BADGE = {"error": "ERROR", "warning": "WARNING", "info": "INFO"}


def _c(text: str, color: str) -> str:
    if not USE_COLOR or not color:
        return text
    return f"{C.get(color, '')}{text}{C['reset']}"


def _fmt_value(value: Any) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return " / ".join(str(v) for v in value)
    return str(value)


def _header(ctx: ReviewContext) -> list[str]:
    w, h = ctx.paper_mm
    frame = "A3" if abs(w - 420) < 1 and abs(h - 297) < 1 else f"{w:g}x{h:g}"
    total = sum(ctx.entity_counts.values())
    ext = ""
    if ctx.extents:
        (x0, y0), (x1, y1) = ctx.extents
        ext = f"{x1 - x0:,.0f} x {y1 - y0:,.0f} mm"
    return [
        f"文件    {ctx.path}",
        f"版本    {ctx.dxfversion}   单位 $INSUNITS={ctx.insunits}   LTSCALE={ctx.ltscale:g}",
        f"图幅    {frame}（LIMITS {w:g}x{h:g}）   出图比例 1:{ctx.scale}（由 --scale 指定）",
        f"内容    {total} 个实体   尺寸标注 {ctx.dim_count} 个   图层 {len(ctx.layers)} 个   "
        f"范围 {ext or '未知'}",
        f"布局    {', '.join(ctx.layout_names)}   块定义 {len(ctx.block_names)} 个",
    ]


def render_text(ctx: ReviewContext, results: list[tuple[Rule, list[Finding]]],
                clauses: dict[str, Clause], version: str = "") -> str:
    out: list[str] = []
    out.append(_c("=" * 78, "dim"))
    out.append(_c(f"  cadspec {version}  中国建筑制图规范 · 图纸合规报告", "bold"))
    out.append(_c("=" * 78, "dim"))
    out.extend(_header(ctx))
    out.append("")

    by_level: dict[str, list[tuple[Rule, Finding]]] = {"error": [], "warning": [], "info": []}
    passed: list[Rule] = []
    for rule, findings in results:
        if not findings:
            passed.append(rule)
            continue
        for f in findings:
            by_level[f.severity.value].append((rule, f))

    for level in ("error", "warning", "info"):
        items = by_level[level]
        if not items:
            continue
        color = LEVEL_COLOR[level]
        out.append(_c(f"── {LEVEL_BADGE[level]}  {len(items)} 条 " + "─" * 44, color))
        for rule, f in items:
            out.append(_c(f"[{f.rule_id}] {rule.title}", "bold"))
            if f.cite and f.cite in clauses:
                cl = clauses[f.cite]
                out.append(_c(f"  依据  {cl.standard} §{cl.clause_id.split('/')[-1]}"
                              f"《{cl.standard_name}》{cl.chapter}", "dim"))
            elif f.cite:
                out.append(_c(f"  依据  {f.cite}（未在知识库中找到）", "dim"))
            else:
                out.append(_c("  依据  无直接规范条号（工程约定）", "dim"))
            for line in _wrap(f.message, 74):
                out.append("  " + line)
            if f.actual is not None:
                out.append(_c(f"  实际  {_fmt_value(f.actual)}", "dim"))
            if f.expected is not None:
                out.append(_c(f"  期望  {_fmt_value(f.expected)}", "dim"))
            if f.handle:
                loc = f" @ ({f.location[0]:.0f}, {f.location[1]:.0f})" if f.location else ""
                out.append(_c(f"  定位  handle={f.handle}{loc}", "dim"))
            elif f.sample:
                out.append(_c(f"  样本  {_fmt_value(f.sample)}", "dim"))
            if f.count > 1 and f.sample:
                out.append(_c(f"  合计  {f.count} 处", "dim"))
            out.append("")

    if passed:
        out.append(_c(f"── 通过  {len(passed)} 条 " + "─" * 48, "green"))
        for rule in passed:
            out.append(_c(f"  ✓ [{rule.rule_id}] {rule.title}", "green"))
        out.append("")

    no_cite = [r for r, fs in results if any(f.cite is None and f.severity != Severity.INFO for f in fs)]
    missing = [r for r in [rule for rule, _ in results] if r.cite is None]
    if missing:
        out.append(_c("── 依据待补 " + "─" * 48, "dim"))
        for rule in missing:
            out.append(_c(f"  · [{rule.rule_id}] {rule.title}", "dim"))
            if rule.note:
                for line in _wrap(rule.note, 72):
                    out.append(_c("    " + line, "dim"))
        out.append("")

    if ctx.completeness:
        comp = ctx.completeness
        items = comp.get("items") or []
        bad = [i for i in items if i["ok"] is False]
        nok = [i for i in items if i["ok"] is None]
        okn = comp.get("ok", 0)
        tot = comp.get("applicable", 0)
        out.append(_c(f"── 图面完整性  {okn}/{tot} 项齐备 " + "─" * 34, "bold"))
        for i in bad:
            out.append(_c(f"  ✗ {i['label']}  {i['detail']}", "bold"))
            if i.get("unlocks"):
                for line in _wrap(f"→ 补齐后可判：{i['unlocks']}", 70):
                    out.append(_c("      " + line, "dim"))
        for i in nok:
            out.append(_c(f"  ? {i['label']}  {i['detail']}", "dim"))
        if not bad and not nok:
            out.append(_c("  该有的都有了。", "green"))
        out.append("")

    n_err = len(by_level["error"])
    n_warn = len(by_level["warning"])
    n_info = len(by_level["info"])
    out.append(_c("=" * 78, "dim"))
    summary = (f"  合计  error {n_err}   warning {n_warn}   info {n_info}   "
               f"通过 {len(passed)}/{len(results)} 条规则")
    out.append(_c(summary, "bold"))
    out.append(_c("=" * 78, "dim"))
    return "\n".join(out)


def _wrap(text: str, width: int) -> list[str]:
    """按显示宽度折行，中文按 2 算。"""
    lines, cur, cur_w = [], "", 0
    for ch in text:
        w = 2 if ord(ch) > 0x2E80 else 1
        if cur_w + w > width and cur:
            lines.append(cur)
            cur, cur_w = "", 0
        cur += ch
        cur_w += w
    if cur:
        lines.append(cur)
    return lines or [""]


def render_markdown(ctx: ReviewContext, results: list[tuple[Rule, list[Finding]]],
                    clauses: dict[str, Clause]) -> str:
    out: list[str] = []
    out.append(f"## 图纸合规报告：`{ctx.path}`\n")
    out.append("| 项目 | 值 |\n|---|---|")
    for line in _header(ctx):
        parts = line.split(None, 1)
        out.append(f"| {parts[0]} | {parts[1] if len(parts) > 1 else ''} |")
    out.append("")

    for level in ("error", "warning", "info"):
        items = [(r, f) for r, f in [(r, f) for r, fs in results for f in fs]
                 if f.severity.value == level]
        if not items:
            continue
        out.append(f"### {LEVEL_BADGE[level]}（{len(items)}）\n")
        for rule, f in items:
            cite = ""
            if f.cite and f.cite in clauses:
                cl = clauses[f.cite]
                cite = f" — {cl.standard} §{cl.clause_id.split('/')[-1]}"
            out.append(f"**[{f.rule_id}] {rule.title}**{cite}\n")
            out.append(f"> {f.message}\n")
            if f.expected is not None:
                out.append(f"- 期望：`{_fmt_value(f.expected)}`")
            if f.actual is not None:
                out.append(f"- 实际：`{_fmt_value(f.actual)}`")
            if f.handle:
                out.append(f"- 定位：`handle={f.handle}`")
            if f.count > 1:
                out.append(f"- 合计：{f.count} 处")
            out.append("")

    if ctx.completeness:
        comp = ctx.completeness
        items = comp.get("items") or []
        bad = [i for i in items if i["ok"] is False]
        nok = [i for i in items if i["ok"] is None]
        out.append(f"### 图面完整性（{comp.get('ok', 0)}/{comp.get('applicable', 0)}）\n")
        if not bad and not nok:
            out.append("该有的都有了。\n")
        if bad:
            out.append("| 缺什么 | 现状 | 补齐后可判 |\n|---|---|---|")
            for i in bad:
                out.append(f"| {i['label']} | {i['detail']} | {i.get('unlocks') or '—'} |")
            out.append("")
        for i in nok:
            out.append(f"- _{i['label']}：{i['detail']}_")
        out.append("")

    return "\n".join(out)


def render_json(ctx: ReviewContext, results: list[tuple[Rule, list[Finding]]]) -> str:
    payload = {
        "file": ctx.path,
        "dxfversion": ctx.dxfversion,
        "insunits": ctx.insunits,
        "scale": ctx.scale,
        "paper_mm": list(ctx.paper_mm),
        "entity_counts": ctx.entity_counts,
        "dim_count": ctx.dim_count,
        "geometry_level": ctx.geometry_level,
        "geometry_note": ctx.geometry_note,
        "completeness": ctx.completeness,
        "rooms": ctx.rooms,
        "openings": ctx.openings,
        "findings": [
            {
                "rule_id": f.rule_id,
                "severity": f.severity.value,
                "cite": f.cite,
                "message": f.message,
                "handle": f.handle,
                "location": list(f.location) if f.location else None,
                "expected": f.expected,
                "actual": f.actual,
                "sample": f.sample,
                "count": f.count,
            }
            for _rule, findings in results for f in findings
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
