# -*- coding: utf-8 -*-
"""规则实现。每个 check 函数签名 (ctx, params) -> list[Finding]。

约定：
  - 只读 ctx，不改 ctx，不碰 DXF 文件。
  - 同一类问题合并成一个 Finding（count + sample），避免一份图报两百条把用户淹掉。
  - 无法确定时报 info 并在 message 里说明缺什么条件，不要假装确定。
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .model import Finding, ReviewContext, Rule, Severity

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
SEP = "-"

CHECKS: dict[str, Callable[[ReviewContext, dict], list[Finding]]] = {}


def check(name: str):
    def deco(fn):
        CHECKS[name] = fn
        return fn
    return deco


def _f(rule: Rule | None, msg: str, **kw) -> Finding:
    """构造 Finding。

    rule 传 None 时只给占位值，真正的 rule_id / severity / cite 由 run_rules 回填。
    哪些规则复用同一个 check 函数，就在哪条规则下取哪条规则的身份——
    函数不需要知道自己被谁调用。
    """
    if rule is None:
        kw.setdefault("severity", Severity.INFO)
        return Finding(rule_id="", message=msg, **kw)
    kw.setdefault("severity", rule.severity)
    kw.setdefault("cite", rule.cite)
    return Finding(rule_id=rule.rule_id, message=msg, **kw)


# ---------------------------------------------------------------- 前置条件

@check("unit_not_mm")
def unit_not_mm(ctx: ReviewContext, params: dict) -> list[Finding]:
    allowed = params.get("allowed", [4, 0])
    if ctx.insunits in allowed:
        return []
    names = {1: "英寸", 2: "英尺", 5: "厘米", 6: "米", 3: "英里", 8: "微米"}
    return [Finding(
        rule_id="R-UNIT-001",
        severity=Severity.ERROR,
        message=(
            f"图形单位 $INSUNITS={ctx.insunits}（{names.get(ctx.insunits, '未知')}），不是毫米。"
            "本工具所有几何判定（线宽、字高、轴号圆直径）都以 mm 为前提，单位错误时其余结论全部失效。"
        ),
        actual=ctx.insunits,
        expected=allowed,
    )]


# ---------------------------------------------------------------- 图层

@check("layer_script_mixing")
def layer_script_mixing(ctx: ReviewContext, params: dict) -> list[Finding]:
    system = set(params.get("system_layers", []))
    mixed: list[dict] = []
    cjk_only: list[str] = []
    latin_only: list[str] = []
    for lyr in ctx.layers:
        name = lyr["name"]
        if name in system:
            continue
        has_cjk, has_latin = bool(CJK_RE.search(name)), bool(LATIN_RE.search(name))
        if has_cjk and has_latin:
            mixed.append(lyr)
        elif has_cjk:
            cjk_only.append(name)
        elif has_latin:
            latin_only.append(name)

    findings: list[Finding] = []
    if mixed:
        findings.append(Finding(
            rule_id="R-LAYER-001",
            severity=Severity.WARNING,
            cite="GBT50001-2017/13.0.1",
            message=(
                f"{len(mixed)} 个图层名单内汉字与字母混排。§13.0.1 第 2 款用"
                "\"不宜\"，属推荐性，不判错，但会导致按名称检索/批量选择失效。"
            ),
            actual=[m["name"] for m in mixed],
            expected="单层名只用汉字、字母、数字、连字符中的一类字符体系",
            sample=[m["name"] for m in mixed[:8]],
            count=len(mixed),
        ))
    if cjk_only and latin_only:
        findings.append(Finding(
            rule_id="R-LAYER-001",
            severity=Severity.ERROR,
            cite="GBT50001-2017/13.0.1",
            message=(
                f"同一张图内汉字图层 {len(cjk_only)} 个、字母图层 {len(latin_only)} 个并存。"
                "§13.0.1 第 3 款用\"不应\"，属强制：同一工程不应同时使用汉字和字母两种命名格式。"
            ),
            actual={"中文图层": cjk_only[:6], "英文图层": latin_only[:6]},
            expected="整张图统一为中文命名或统一为英文命名",
            sample=cjk_only[:4] + latin_only[:4],
            count=len(cjk_only) + len(latin_only),
        ))
    return findings


@check("layer_naming_scheme")
def layer_naming_scheme(ctx: ReviewContext, params: dict) -> list[Finding]:
    system = {"0", "Defpoints"}
    min_fields = params.get("min_fields", 2)
    sep = params.get("separator", SEP)
    bad = []
    for lyr in ctx.layers:
        if lyr["name"] in system:
            continue
        fields = [p for p in lyr["name"].split(sep) if p]
        if len(fields) < min_fields:
            bad.append(lyr["name"])
    if not bad:
        return []
    return [Finding(
        rule_id="R-LAYER-002",
        severity=Severity.WARNING,
        cite="GBT50001-2017/13.0.2",
        message=(
            f"{len(bad)} 个图层名是单字段（如\"外墙\"），未采用 §13.0.2 推荐的"
            f"\"专业代码{sep}主代码{sep}次代码\"分级格式。该条全为\"宜\"，不算错误，"
            "但不利于跨专业协同与程序化筛选。"
        ),
        actual=bad[:10],
        expected=f">= {min_fields} 个字段，用 \"{sep}\" 分隔",
        sample=bad[:8],
        count=len(bad),
    )]


@check("layer_lineweight_unset")
def layer_lineweight_unset(ctx: ReviewContext, params: dict) -> list[Finding]:
    system = {"0", "Defpoints"}
    max_ratio = params.get("max_unset_ratio", 0.8)
    considered = [l for l in ctx.layers if l["name"] not in system]
    if not considered:
        return []
    unset = [l["name"] for l in considered if int(l["lineweight"]) < 0]
    ratio = len(unset) / len(considered)
    if ratio < max_ratio:
        return []
    return [Finding(
        rule_id="R-LW-001",
        severity=Severity.WARNING,
        cite="GBT50001-2017/4.0.1",
        message=(
            f"{len(unset)}/{len(considered)} 个图层的线宽都是 DEFAULT（未设置）。"
            "§4.0.1 条文说明强调\"图线线宽均指打印成品图纸的线宽\"，即图层里应直接填 "
            "0.5/0.7/1.0/1.4 这类打印线宽。留 DEFAULT 意味着线宽完全由打印样式表(CTB)决定，"
            "换打印机或换 CTB 后图纸外观不可复现。若本项目确实用 CTB 统一控制，可忽略本条。"
        ),
        actual=f"{len(unset)} 个图层 lineweight=-3",
        expected="按 b 值（0.5/0.7/1.0/1.4mm）给各图层设置显式打印线宽",
        sample=unset[:8],
        count=len(unset),
    )]


# ---------------------------------------------------------------- 定位轴线

def _axis_labels(ctx: ReviewContext) -> list[dict]:
    return [t for t in ctx.texts if t.get("is_axis_label")]


@check("axis_forbidden_letters")
def axis_forbidden_letters(ctx: ReviewContext, params: dict) -> list[Finding]:
    forbidden = set(params.get("forbidden", ["I", "O", "Z"]))
    hits = []
    for t in _axis_labels(ctx):
        label = t["text"]
        if "/" in label:
            continue
        core = label.rstrip("0123456789")
        if core in forbidden or label in forbidden:
            hits.append(t)
    if not hits:
        return []
    return [Finding(
        rule_id="R-AXIS-002",
        severity=Severity.ERROR,
        cite="GBT50001-2017/8.0.4",
        message=(
            f"轴线编号使用了禁用字母 {sorted(forbidden)} 中的字符。§8.0.4 用\"不得\"，"
            "强制禁止：I/O/Z 与数字 1/0/2 形近，图纸缩小或复印后必然误读。"
            "正确做法是跳过该字母继续编号（A B C D E F G H J）。"
        ),
        actual=[t["text"] for t in hits],
        expected="不使用 I、O、Z 作轴线编号",
        sample=[t["text"] for t in hits],
        handle=hits[0]["handle"],
        location=(hits[0]["x"], hits[0]["y"]),
        count=len(hits),
    )]


@check("axis_circle_diameter")
def axis_circle_diameter(ctx: ReviewContext, params: dict) -> list[Finding]:
    if not ctx.scale:
        return []
    lo = float(params.get("min_mm", 8.0)) - float(params.get("tolerance_mm", 0.5))
    hi = float(params.get("max_mm", 10.0)) + float(params.get("tolerance_mm", 0.5))
    bad, ok = [], 0
    for t in _axis_labels(ctx):
        r = t.get("circle_radius")
        if r is None:
            continue
        d_print = 2.0 * r / ctx.scale
        if lo <= d_print <= hi:
            ok += 1
        else:
            bad.append((t["text"], round(d_print, 2)))
    if not bad:
        return []
    return [Finding(
        rule_id="R-AXIS-001",
        severity=Severity.WARNING,
        cite="GBT50001-2017/8.0.2",
        message=(
            f"{len(bad)} 个轴号圆圈按 1:{ctx.scale} 出图后直径为 {bad[0][1]}mm，"
            f"不在 §8.0.2 的 8mm～10mm 范围。注意该尺寸指打印图纸上的尺寸："
            f"1:{ctx.scale} 时模型空间直径应为 {8*ctx.scale}～{10*ctx.scale}mm。"
        ),
        actual=[f"{name}: {d}mm" for name, d in bad[:8]],
        expected=f"{lo}～{hi}mm（打印尺寸）",
        sample=[f"{name}: {d}mm" for name, d in bad[:8]],
        handle=None,
        count=len(bad),
    )]


@check("axis_additional_fraction")
def axis_additional_fraction(ctx: ReviewContext, params: dict) -> list[Finding]:
    labels = _axis_labels(ctx)
    added = [t for t in labels if "/" in t["text"]]
    if not added:
        return []

    # 分母轴（前一轴线）是否存在、附加轴是否在其外侧
    by_label: dict[str, list[dict]] = {}
    for t in labels:
        by_label.setdefault(t["text"], []).append(t)

    notes = []
    for t in added:
        num, den = t["text"].split("/", 1)
        ref = by_label.get(den)
        if not ref:
            notes.append(f"{t['text']}: 分母轴线 {den} 在图中未找到")
            continue
        ry = sum(r["y"] for r in ref) / len(ref)
        side = "外侧" if abs(t["y"] - ry) > 1e-6 else "同位置"
        notes.append(f"{t['text']}(y={t['y']:.0f}, {den}轴 y={ry:.0f}, {side})")

    return [Finding(
        rule_id="R-AXIS-003",
        severity=Severity.WARNING,
        cite="GBT50001-2017/8.0.6",
        message=(
            f"{len(added)} 个附加轴线编号写成了斜杠文本（如 \"1/D\"）。§8.0.6 要求"
            "\"以分数形式表示\"，即分子在上、分母在下的叠排真分数，斜杠文本能读懂但不合形式。"
            "另外：若附加轴位于 1 号或 A 号轴线之前，分母须写 01 或 0A。"
        ),
        actual=notes[:8],
        expected="叠排分数形式：分子=附加轴线号，分母=前一轴线号",
        sample=[t["text"] for t in added],
        handle=added[0]["handle"],
        location=(added[0]["x"], added[0]["y"]),
        count=len(added),
    )]


@check("axis_order")
def axis_order(ctx: ReviewContext, params: dict) -> list[Finding]:
    labels = _axis_labels(ctx)
    findings: list[Finding] = []

    # 横向：阿拉伯数字，从左至右递增
    nums: dict[str, list[tuple[float, float]]] = {}
    for t in labels:
        if t["text"].isdigit():
            nums.setdefault(t["text"], []).append((t["x"], t["y"]))
    if len(nums) >= 2:
        ordered = sorted(nums.items(), key=lambda kv: sum(p[0] for p in kv[1]) / len(kv[1]))
        seq = [int(k) for k, _ in ordered]
        if any(b <= a for a, b in zip(seq, seq[1:])):
            findings.append(Finding(
                rule_id="R-AXIS-004",
                severity=Severity.ERROR,
                cite="GBT50001-2017/8.0.3",
                message=(
                    "横向轴线编号没有按从左至右递增。§8.0.3 规定横向用阿拉伯数字从左至右顺序编写，"
                    "方向反了会导致读图者整栋定位错位。"
                ),
                actual=seq,
                expected="从左至右严格递增",
            ))

    # 竖向：大写字母，从下至上递增
    letters: dict[str, list[tuple[float, float]]] = {}
    for t in labels:
        lab = t["text"]
        if "/" in lab or lab.isdigit():
            continue
        if re.fullmatch(r"[A-Z]{1,2}", lab):
            letters.setdefault(lab, []).append((t["x"], t["y"]))
    if len(letters) >= 2:
        ordered = sorted(letters.items(), key=lambda kv: sum(p[1] for p in kv[1]) / len(kv[1]))
        seq = [k for k, _ in ordered]
        if seq != sorted(seq):
            findings.append(Finding(
                rule_id="R-AXIS-004",
                severity=Severity.ERROR,
                cite="GBT50001-2017/8.0.3",
                message=(
                    "竖向轴线编号没有按从下至上递增。§8.0.3 规定竖向用大写拉丁字母从下至上顺序编写。"
                ),
                actual=seq,
                expected="从下至上按字母序递增",
            ))
    return findings


# ---------------------------------------------------------------- 文字

@check("text_height_printed")
def text_height_printed(ctx: ReviewContext, params: dict) -> list[Finding]:
    if not ctx.scale:
        return []
    min_mm = float(params.get("min_mm", 2.5))
    bad = []
    for t in ctx.texts:
        if not t.get("is_alnum"):
            continue
        if t["height"] <= 0:
            continue
        if t["height_printed"] < min_mm:
            bad.append(t)
    if not bad:
        return []

    # 按 (图层, 模型字高) 归并，避免 8 个标高数字报 8 条
    groups: dict[tuple, list[dict]] = {}
    for t in bad:
        groups.setdefault((t["layer"], round(t["height"], 2)), []).append(t)
    parts = []
    for (layer, h), items in groups.items():
        parts.append(f"{layer} 层 {h:g}mm 模型字高×{len(items)}处（打印 {h/ctx.scale:.2f}mm）")

    return [Finding(
        rule_id="R-TEXT-001",
        severity=Severity.ERROR,
        cite="GBT50001-2017/5.0.7",
        message=(
            f"{len(bad)} 处字母/数字文本按 1:{ctx.scale} 出图后字高小于 {min_mm}mm。"
            "§5.0.7 用\"不应\"，属强制（禁止性）。"
            "该条尺寸指打印成品图纸，换算关系是：模型字高 ÷ 出图比例 = 打印字高。"
            f"若按 1:{ctx.scale} 出图，模型字高需 ≥{int(min_mm*ctx.scale)}mm。"
        ),
        actual=parts[:6],
        expected=f"打印字高 >= {min_mm}mm（1:{ctx.scale} 时模型字高 >= {int(min_mm*ctx.scale)}mm）",
        sample=[f"{t['text']}@{t['layer']}" for t in bad[:8]],
        handle=bad[0]["handle"],
        location=(bad[0]["x"], bad[0]["y"]),
        count=len(bad),
    )]


@check("text_layer_mismatch")
def text_layer_mismatch(ctx: ReviewContext, params: dict) -> list[Finding]:
    keys = [k.lower() for k in params.get("expected_layer_keywords", [])]
    if not keys:
        return []
    hits = [t for t in ctx.texts
            if not any(k in t["layer"].lower() for k in keys)]
    if not hits:
        return []
    by_layer: dict[str, list[dict]] = {}
    for t in hits:
        by_layer.setdefault(t["layer"], []).append(t)
    detail = [f"{layer} 层 {len(items)} 处（如 \"{items[0]['text']}\"）"
              for layer, items in by_layer.items()]
    return [Finding(
        rule_id="R-TEXT-002",
        severity=Severity.INFO,
        cite=None,
        message=(
            f"{len(hits)} 处文字放在非文字类图层：" + "；".join(detail) +
            "。建议文字统一放文字/标注/标高层，否则冻结或按层关闭时会连带丢失，"
            "也不利于批量修改字体。此条无直接规范条号，属工程约定。"
        ),
        actual=detail,
        expected="文字位于文字/标注/标高类图层",
        sample=[f"{t['text']}@{t['layer']}" for t in hits[:8]],
        count=len(hits),
    )]


# ---------------------------------------------------------------- 其他

@check("no_dimension_entities")
def no_dimension_entities(ctx: ReviewContext, params: dict) -> list[Finding]:
    if ctx.dim_count > 0:
        return []
    return [Finding(
        rule_id="R-DIM-001",
        severity=Severity.ERROR,
        message=(
            "图中没有任何 DIMENSION 实体，尺寸标注数量为 0。《深度规定》4.3.4-1 要求"
            "平面图标注轴线总尺寸（或外包总尺寸）、轴线间尺寸、门窗洞口尺寸、分段尺寸。"
            "若尺寸是用 LINE + TEXT 手工拼出来的，本规则会误报，请人工确认。"
        ),
        actual=0,
        expected="存在尺寸标注",
        count=1,
    )]


@check("drawing_scale_missing")
def drawing_scale_missing(ctx: ReviewContext, params: dict) -> list[Finding]:
    pattern = params.get("pattern") or r"1\s*[:：∶]\s*\d+"
    rx = re.compile(pattern)
    hits = [t for t in ctx.texts if rx.search(t.get("text") or "")]
    if hits:
        return []
    return [Finding(
        rule_id="R-DIM-002",
        severity=Severity.WARNING,
        message=(
            "没有找到比例标注（形如 1:100）。《深度规定》4.3.4-20 要求注明图纸名称、比例；"
            "字高、线宽、轴号圆直径是否合规全部以出图比例为基准，比例缺失时只能依赖"
            "命令行传入的 --scale 假设值。若比例写在布局视口、标题栏属性块或外部参照中，"
            "本规则会误报。"
        ),
        actual="未检出比例文字",
        expected="图面出现 1:100 之类的比例标注",
        count=1,
    )]


# ---------------------------------------------------------------- 图层与图面完整性

@check("layer_declared_but_empty")
def layer_declared_but_empty(ctx: ReviewContext, params: dict) -> list[Finding]:
    """定义了却没有任何实体的图层。模板残留、改图后忘清理的典型痕迹。"""
    ignore = set(params.get("ignore", ["0", "Defpoints"]))
    used = {name for name, counts in ctx.layer_entity_counts.items() if any(counts.values())}
    empties = [l for l in ctx.layers if l["name"] not in ignore and l["name"] not in used]
    if not empties:
        return []
    detail = "、".join(f"{l['name']}(色{l['color']})" for l in empties)
    return [_f(
        None,  # type: ignore[arg-type]
        f"{len(empties)} 个图层已定义但没有任何实体：{detail}。"
        "可能是模板残留、改图后忘了清理，也可能是这张图还没画到那一步。"
        "《房屋建筑制图统一标准》13.0.1 要求图层按用途组织、命名保持统一；"
        "空图层会让接手的人和 AI 误判这张图还有哪些内容。"
        "本规则只统计模型空间，图层若只用在布局或外部参照里会误报。",
        sample=[l["name"] for l in empties],
        count=len(empties),
        expected="每个已定义的图层都承载实体",
        actual=f"{len(empties)} 个空图层",
    )]


@check("drawing_scale_consistency")
def drawing_scale_consistency(ctx: ReviewContext, params: dict) -> list[Finding]:
    """图面写的出图比例与本次计算的假设比例是否一致。"""
    pattern = params.get("pattern") or r"1\s*[:：∶]\s*(\d{1,4})"
    rx = re.compile(pattern)
    found: list[tuple[dict, int]] = []
    for t in ctx.texts:
        m = rx.search(t.get("text") or "")
        if m:
            found.append((t, int(m.group(1))))
    if not found:
        return []
    wrong = [(t, n) for t, n in found if n != ctx.scale]
    if not wrong:
        return []
    detail = "、".join(f"「{(t.get('text') or '').strip()}」(应在 {n}:1)" for t, n in wrong[:4])
    return [_f(
        None,  # type: ignore[arg-type]
        f"图面标注的比例与本次计算采用的比例不一致：图里写的是 {detail}，"
        f"而所有打印尺寸（字高、线宽、轴号圆直径）都按 1:{ctx.scale} 换算。"
        "《深度规定》4.3.4-20 要求注明图纸名称与比例，比例写错会让字高、线宽、"
        "甚至楼梯踏步的合规结论整体偏移一个档位。"
        "若这张图确实要按图面比例出图，请改用 --scale 重新检查。",
        sample=[(t.get("text") or "").strip() for t, _ in wrong[:8]],
        count=len(wrong),
        expected=f"图面比例 = 1:{ctx.scale}",
        actual=f"图面出现 {len(wrong)} 处不同比例",
    )]


@check("dimension_entities_under_count")
def dimension_entities_under_count(ctx: ReviewContext, params: dict) -> list[Finding]:
    """尺寸标注实体数量低于下限。与 no_dimension_entities 的分工：
    那条只判「一个都没有」，这条判「有但不够」。"""
    need = int(params.get("min_count", 1))
    if ctx.dim_count >= need:
        return []
    return [_f(
        None,  # type: ignore[arg-type]
        f"图中 DIMENSION 实体只有 {ctx.dim_count} 个，少于要求的 {need} 个。"
        "《深度规定》4.3.4-1 要求平面图同时标注轴线总尺寸（或外包总尺寸）、"
        "轴线间尺寸（柱距、跨度）、门窗洞口尺寸、分段尺寸——这几类尺寸各自成组，"
        "个位数标注通常只覆盖了其中一类。"
        "若尺寸是用 LINE + TEXT 手工拼出来的，本规则会误报，请人工确认。",
        actual=ctx.dim_count,
        expected=f"至少 {need} 个尺寸标注",
        count=1,
    )]


# ---------------------------------------------------------------- 调度

def run_rules(ctx: ReviewContext, rules: list[Rule]) -> list[tuple[Rule, list[Finding]]]:
    results: list[tuple[Rule, list[Finding]]] = []
    for rule in rules:
        fn = CHECKS.get(rule.check)
        if fn is None:
            results.append((rule, [Finding(
                rule_id=rule.rule_id,
                severity=Severity.INFO,
                message=f"规则声明的 check 函数 {rule.check!r} 未实现，已跳过",
            )]))
            continue
        try:
            found = fn(ctx, rule.params)
        except Exception as exc:  # 单条规则崩了不影响整份报告
            results.append((rule, [Finding(
                rule_id=rule.rule_id,
                severity=Severity.INFO,
                message=f"规则执行出错，已跳过：{type(exc).__name__}: {exc}",
            )]))
            continue
        for f in found:
            if f.cite is None:
                f.cite = rule.cite
            # rule_id 一律以规则为准。同一个 check 可以被多条规则复用（不同条文、
            # 不同阈值），函数里硬编码的 id 只在那一条规则下成立，复用就会张冠李戴。
            f.rule_id = rule.rule_id
            # 级别一律以 data/rules 为准，函数里的 severity 只是默认值。
            # 好处：调级别只改 YAML，不用改代码，也不会出现两处悄悄不一致。
            f.severity = rule.severity
        results.append((rule, found))
    return results


# ---------------------------------------------------------------- 房间尺度
#
# 这两条判据都来自 GB 55038《住宅项目规范》4.1 套内空间：
# 卧室使用面积、卧室短边、厨房短边。它们能写成规则，是因为图上有房间名字，
# 而且墙线能围合成多边形——几何给面积，文字给用途，两者一碰就能判。
#
# 一个用途对应一条规则，所以 room_area_below / room_short_side_below
# 各写一次、被多条规则用不同 params 复用。

_MEASURE_NOTE = (
    "面积按墙中心线围合后内缩半个墙厚得到，即净使用面积；墙厚取围合该房间墙段的中位数，"
    "墙厚不一致时是近似值。未扣柱、管道井与凸窗。"
    "房间用途取自图内文字，图上没写名字的房间不参与判定。"
)


def _rooms_of(ctx: ReviewContext, params: dict) -> list[dict]:
    purpose = params.get("purpose")
    if not purpose:
        return []
    return [r for r in ctx.rooms if r.get("purpose") == purpose]


def _room_name(r: dict) -> str:
    return r.get("label") or r.get("purpose") or f"房间#{r['index']}"


@check("room_area_below")
def room_area_below(ctx: ReviewContext, params: dict) -> list[Finding]:
    """指定用途的房间，净使用面积低于条文下限。"""
    rooms = _rooms_of(ctx, params)
    if not rooms:
        return []
    min_area = float(params.get("min_area_m2", 0))
    out: list[Finding] = []
    for r in rooms:
        if r["net_area_m2"] >= min_area:
            continue
        cx, cy = r["center"]
        out.append(_f(
            None,  # type: ignore[arg-type]
            f"「{_room_name(r)}」净使用面积 {r['net_area_m2']:.2f}m²，"
            f"小于 {min_area:g}m²（房间中心 {cx:.0f},{cy:.0f}）。"
            f"{_MEASURE_NOTE}",
            location=(cx, cy),
            expected=f"≥ {min_area:g}m²",
            actual=f"{r['net_area_m2']:.2f}m²",
        ))
    return out


@check("room_short_side_below")
def room_short_side_below(ctx: ReviewContext, params: dict) -> list[Finding]:
    """指定用途的房间，最小短边低于条文下限。"""
    rooms = _rooms_of(ctx, params)
    if not rooms:
        return []
    min_side = float(params.get("min_side_mm", 0))
    out: list[Finding] = []
    for r in rooms:
        if r["net_short_side"] >= min_side:
            continue
        cx, cy = r["center"]
        out.append(_f(
            None,  # type: ignore[arg-type]
            f"「{_room_name(r)}」净最小短边 {r['net_short_side']:.0f}mm，"
            f"小于 {min_side:.0f}mm（房间中心 {cx:.0f},{cy:.0f}）。"
            "短边取房间最小外接矩形的短边，即横竖两个方向里较窄的那个净尺寸；"
            "L 形房间按外接矩形算，会低估实际的最窄处，这种房间需要人工复核。",
            location=(cx, cy),
            expected=f"≥ {min_side:.0f}mm",
            actual=f"{r['net_short_side']:.0f}mm",
        ))
    return out


# ---------------------------------------------------------------- 门窗
#
# 门窗在图上只是墙上的一个洞。要判门宽，先得知道哪个洞是门。
# 判断靠 traits（图层名/样式名/块名/弧线/编号），全都拿不到时判 unknown。
# 这一组规则做两件事：把判断不出来的如实报出来，以及判能判出来的门宽。


def _openings(ctx: ReviewContext, kinds: tuple[str, ...]) -> list[dict]:
    real = [o for o in ctx.openings if o.get("filled")]
    return [o for o in real if o.get("kind") in kinds]


@check("opening_kind_unknown")
def opening_kind_unknown(ctx: ReviewContext, params: dict) -> list[Finding]:
    """洞口既不像门也不像窗——不是图错了，是图面表达不足以支撑判断。"""
    unknown = _openings(ctx, ("unknown",))
    if not unknown:
        return []
    detail = "、".join(f"{o['width']:.0f}mm" for o in unknown)
    return [_f(
        None,  # type: ignore[arg-type]
        f"{len(unknown)} 处洞口无法区分门窗（宽度 {detail}）。"
        "门窗在图上都是墙上的洞，要靠图层名、多线样式名、块名、门扇弧线或编号文字才能区分；"
        "这几样都没有时，无法核对门洞净宽（户门 0.90m、卧室门 0.80m、厨卫门 0.70m）。"
        "把门窗分层、或给门窗加编号（M1 / C1），本工具就能继续判。",
        sample=[f"{o['width']:.0f}mm @ ({o['start'][0]:.0f},{o['start'][1]:.0f})"
                for o in unknown],
        count=len(unknown),
        expected="每处洞口都能判定为门或窗",
        actual=f"{len(unknown)} 处未判定",
    )]


@check("door_width_below")
def door_width_below(ctx: ReviewContext, params: dict) -> list[Finding]:
    """门洞宽度低于条文下限。"""
    doors = _openings(ctx, ("door",))
    if not doors:
        return []
    min_w = float(params.get("min_width_mm", 740))
    frame = float(params.get("frame_allowance_mm", 40))
    out: list[Finding] = []
    for o in doors:
        if o["width"] >= min_w:
            continue
        cx = (o["start"][0] + o["end"][0]) / 2
        cy = (o["start"][1] + o["end"][1]) / 2
        out.append(_f(
            None,  # type: ignore[arg-type]
            f"门洞宽度 {o['width']:.0f}mm，小于 {min_w:.0f}mm（洞口中心 {cx:.0f},{cy:.0f}）。"
            f"判定依据：{o.get('evidence') or '未记录'}。"
            f"这里量的是洞口宽度。规范要的是「通行净宽」——门扇开启后的净开口，"
            f"每边扣门框约 {frame / 2:.0f}mm，所以洞口至少要 {min_w:.0f}mm 才能留出 700mm 净宽。"
            "洞口 700 的厨卫门，净宽实际只有 660 左右，不满足。",
            location=(cx, cy),
            expected=f"≥ {min_w:.0f}mm（洞口宽）",
            actual=f"{o['width']:.0f}mm",
            handle=None,
        ))
    return out


def _exterior_doors(ctx: ReviewContext, tol: float) -> list[dict]:
    """外轮廓上的门。外轮廓取所有房间的并集边界。"""
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union

    try:
        shell = unary_union([Polygon(r["outline"]) for r in ctx.rooms])
    except Exception:
        return []
    edge = shell.boundary
    out = []
    for o in _openings(ctx, ("door",)):
        mx = (o["start"][0] + o["end"][0]) / 2
        my = (o["start"][1] + o["end"][1]) / 2
        if edge.distance(Point(mx, my)) <= tol:
            out.append(o)
    return out


@check("entrance_door_width_below")
def entrance_door_width_below(ctx: ReviewContext, params: dict) -> list[Finding]:
    """户门太窄。量的是洞口宽，条文要的是通行净宽，所以阈值要先加上门框。"""
    if ctx.geometry_level != "strict" or not ctx.rooms:
        return []
    min_net = float(params.get("min_net_width_mm", 0))
    frame = float(params.get("frame_allowance_mm", 40))
    min_w = min_net + frame
    tol = float(params.get("edge_tolerance_mm", 150))

    out: list[Finding] = []
    for o in _exterior_doors(ctx, tol):
        if o["width"] >= min_w:
            continue
        mx = (o["start"][0] + o["end"][0]) / 2
        my = (o["start"][1] + o["end"][1]) / 2
        out.append(_f(
            None,  # type: ignore[arg-type]
            f"户门洞口 {o['width']:.0f}mm，小于 {min_w:.0f}mm。"
            f"规范要的是通行净宽 {min_net:.0f}mm，洞口扣两侧门框（按每边 "
            f"{frame / 2:.0f}mm 估）后只剩 {o['width'] - frame:.0f}mm 左右。",
            location=(mx, my),
            expected=f"≥ {min_w:.0f}mm（洞口宽）",
            actual=f"{o['width']:.0f}mm",
        ))
    return out


@check("door_width_below_for_room")
def door_width_below_for_room(ctx: ReviewContext, params: dict) -> list[Finding]:
    """通向指定用途房间的门太窄。

    用途不同门槛不同：卧室门 0.80m、厨卫门 0.70m、户门 0.90m。
    统一按最低档判会放过卧室门，所以要先知道这扇门通向哪。
    """
    if ctx.geometry_level != "strict" or not ctx.rooms:
        return []
    purpose = params.get("purpose")
    if not purpose:
        return []
    min_net = float(params.get("min_net_width_mm", 0))
    frame = float(params.get("frame_allowance_mm", 40))
    min_w = min_net + frame

    doors = {o.get("oid"): o for o in _openings(ctx, ("door",))}
    out: list[Finding] = []
    seen: set[str] = set()

    for r in ctx.rooms:
        if r.get("purpose") != purpose:
            continue
        for nb in r.get("neighbors") or []:
            if nb.get("kind") != "door":
                continue
            oid = nb.get("opening")
            if oid in seen or oid not in doors:
                continue
            seen.add(oid)
            o = doors[oid]
            if o["width"] >= min_w:
                continue
            mx = (o["start"][0] + o["end"][0]) / 2
            my = (o["start"][1] + o["end"][1]) / 2
            out.append(_f(
                None,  # type: ignore[arg-type]
                f"{purpose}的门洞口 {o['width']:.0f}mm，小于 {min_w:.0f}mm"
                f"（通行净宽需 {min_net:.0f}mm，扣门框 {frame:.0f}mm）。"
                f"所在房间「{_room_name(r)}」。",
                location=(mx, my),
                expected=f"≥ {min_w:.0f}mm（洞口宽）",
                actual=f"{o['width']:.0f}mm",
            ))
    return out


@check("no_exterior_door")
def no_exterior_door(ctx: ReviewContext, params: dict) -> list[Finding]:
    """外轮廓上一个门洞都没有——整套房子没有直接通向室外的出口。

    这条判的是"缺画"，不是"画错"。DXF 里没有的东西，几何算法是看不见的：
    polygonize 只能围出已经画了墙的地方，画漏的墙、没开的门，它一律当作不存在。
    所以这类检查只能反着做——先假定该有，再看有没有。
    """
    if ctx.geometry_level != "strict" or not ctx.rooms:
        return []
    doors = _openings(ctx, ("door",))
    if not doors:
        return []

    tol = float(params.get("edge_tolerance_mm", 150))
    outside = _exterior_doors(ctx, tol)

    if outside:
        return []

    widths = "、".join(f"{o['width']:.0f}mm" for o in doors[:6])
    return [_f(
        None,  # type: ignore[arg-type]
        f"图中的 {len(doors)} 处门（{widths}…）全部位于内墙，外轮廓上一个门洞也没有。"
        "一套住宅至少要有户门通向公共楼梯间或室外，否则疏散路径不成立。"
        "这条是「缺画」检查：如果门确实画了但落在识别范围外（比如画在块里、"
        "或在另一个图层），也会报这一条，请对照图纸确认。",
        sample=[f"{o['width']:.0f}mm @ ({o['start'][0]:.0f},{o['start'][1]:.0f})"
                for o in doors[:6]],
        count=len(doors),
        expected="外轮廓上至少有 1 处门洞",
        actual="0 处",
        handle=None,
    )]
