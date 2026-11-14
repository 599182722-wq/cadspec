# -*- coding: utf-8 -*-
"""图纸可读性体检（doctor）。

这个模块只回答一个问题：**这张图现在能被自动审到什么程度，要改哪几处才能更进一步。**

它不判合规，也不猜几何。它检查的是「图上有没有程序能读懂的语义」——
单位、图层约定、文字标注、构件类型。这些东西缺了，后面的尺度判定就无从谈起，
而硬猜只会产出假阳性，让使用者对整份报告失去信任。

之所以单独成一个命令，是因为现实里的图纸分两类：
一类按约定画（墙在「外墙」「内墙」，门窗在「门窗」，房间写名字），工具能判尺度；
另一类画得很满但没有语义（墙叫 123，全图没有文字），工具只能判「图纸表达」。
把这两类放在同一个报告里，读者会误以为后者「合规」，所以要先把话说明白。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import ezdxf

BLOCKER = "blocker"
ADVISORY = "advisory"
OK = "ok"

#: 图层名像墙的：中英文都认，含「墙」的优先，其次天正常用的 QT/QH。
WALL_NAME_RE = re.compile(r"墙|WALL|QT|QH", re.I)

#: 机器生成或随手起的图层名：纯数字、PDF 导入前缀、天正内部层、加密块前缀。
JUNK_NAME_RE = re.compile(r"^\d+$|^PDF[_-]|^A\$|^_TCH|^TEMP\d*$", re.I)

#: 构件类图层里，这些名字说明画图人想表达什么，但不构成完整的图层体系。
SUGGESTED_LAYERS = {
    "外墙": "承重外墙的墙线",
    "内墙": "隔墙的墙线",
    "门窗": "门、窗洞口",
    "楼梯": "楼梯踏步与梯段",
    "轴线": "定位轴线",
    "标注": "尺寸标注",
}


@dataclass
class Diagnosis:
    code: str
    level: str
    title: str
    found: str = ""
    impact: str = ""
    fix: str = ""

    @property
    def ok(self) -> bool:
        return self.level == OK


@dataclass
class DoctorReport:
    path: str
    items: list[Diagnosis] = field(default_factory=list)

    @property
    def blockers(self) -> list[Diagnosis]:
        return [i for i in self.items if i.level == BLOCKER]

    @property
    def advisories(self) -> list[Diagnosis]:
        return [i for i in self.items if i.level == ADVISORY]

    def capabilities(self) -> dict[str, bool]:
        """这类图能跑哪几批检查。由阻塞项直接推出来，不另设一套判断。"""
        codes = {i.code for i in self.blockers}
        geometry = not (codes & {"B-WALL-LAYER", "B-WALL-ENTITY", "B-UNIT"})
        room = geometry and "B-TEXT" not in codes
        return {
            "expression": True,                      # 图层、线宽、字高、比例
            "geometry": geometry,                    # 房间多边形、洞口
            "room": room,                            # 房间用途与尺度
            "dimension": "B-UNIT" not in codes,      # 尺寸标注类
        }


def _counts(msp) -> dict[str, int]:
    n: dict[str, int] = {}
    for e in msp:
        lay = e.dxf.get("layer", "0")
        n[lay] = n.get(lay, 0) + 1
    return n


def _percentile(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


def _ck_unit(doc) -> Diagnosis:
    ins = doc.header.get("$INSUNITS", 0)
    if ins == 4:
        return Diagnosis("B-UNIT", OK, "图形单位已声明为毫米", found="$INSUNITS=4 (mm)")
    name = {0: "无单位", 1: "英寸", 2: "英尺", 5: "厘米", 6: "米"}.get(ins, f"代码 {ins}")
    return Diagnosis(
        "B-UNIT", BLOCKER, "图形单位未声明为毫米",
        found=f"$INSUNITS={ins}（{name}）",
        impact="所有长度量都失去基准：1:100 下的字高、墙厚、门宽判定全部不可信。"
               "程序只能按 mm 强行读，等于在猜。",
        fix="在 CAD 里执行 UNITS 命令，把「插入比例」设为毫米；"
            "或直接改 DXF 文件头 $INSUNITS 为 4。",
    )


def _ck_text(msp, doc) -> Diagnosis:
    own = len(list(msp.query("TEXT MTEXT")))
    inner = 0
    samples: list[str] = []
    for blk in doc.blocks:
        # 以 * 开头的是匿名块：*D 由尺寸标注生成、*X 是填充、*U 是未命名块。
        # 它们里面的文字是标注数值，不是画图人写的内容，算进来会虚高。
        if blk.name.startswith("*"):
            continue
        ts = list(blk.query("TEXT MTEXT"))
        if ts:
            inner += len(ts)
            for t in ts[:2]:
                s = t.plain_text() if t.dxftype() == "MTEXT" else t.dxf.get("text", "")
                if s.strip():
                    samples.append(s.strip())
    if own:
        return Diagnosis("B-TEXT", OK, "图上有文字对象", found=f"模型空间 {own} 个")
    if inner:
        shown = "、".join(samples[:5]) + ("…" if len(samples) > 5 else "")
        return Diagnosis(
            "B-TEXT", BLOCKER, "文字全在块里，模型空间没有",
            found=f"模型空间 0 个，块定义内 {inner} 个（{shown}）",
            impact="房间名、图名、比例都读不到。块内文字的位置要跟着块引用的插入点、"
                   "缩放和旋转逐层变换才能算出落点，现在不做这层解析。",
            fix="把房间名、图名、比例写成模型空间的 TEXT 或 MTEXT，不要只放在块里。",
        )
    return Diagnosis(
        "B-TEXT", BLOCKER, "全图没有任何文字",
        found="模型空间 0 个，块定义内 0 个（匿名块不计）",
        impact="没有房间名就判不了房间用途，没有图名和比例就判不了出图比例，"
               "依据房间的门窗宽度、面积、采光条文全部失效。",
        fix="至少补三类文字：每个房间写用途（卧室/厨房/卫生间）、图名下写比例（如 1:100）、"
            "首层平面写指北针。",
    )


def _ck_wall_layer(msp, counts: dict[str, int]) -> Diagnosis:
    cands = [l for l in counts if WALL_NAME_RE.search(l)]
    if not cands:
        looks = [l for l in counts if l.upper() in ("123", "0")][:3]
        return Diagnosis(
            "B-WALL-LAYER", BLOCKER, "没有可识别的墙图层",
            found=f"图层名里没有任何含「墙」/WALL/QT 的名字"
                  + (f"；实体最集中的是 {', '.join(looks)}" if looks else ""),
            impact="墙线都认不出来，房间多边形就围不出来，尺度类检查一条都跑不了。",
            fix="把墙线放到名为「外墙」「内墙」的图层上（英文 WALL 也认）。"
                "如果墙分散在好几个图层，先合并到一个。",
        )
    detail = "、".join(f"{l}({counts[l]})" for l in sorted(cands, key=lambda x: -counts[x])[:5])
    return Diagnosis("B-WALL-LAYER", OK, "存在墙图层", found=detail)


def _ck_wall_entity(msp, counts: dict[str, int]) -> Diagnosis:
    wall_layers = [l for l in counts if WALL_NAME_RE.search(l)]
    if not wall_layers:
        return Diagnosis("B-WALL-ENTITY", ADVISORY, "无从判断墙的绘制方式",
                         found="先解决「没有墙图层」")
    n_mline = 0
    for lay in wall_layers:
        n_mline += len(list(msp.query(f'MLINE[layer=="{lay}"]')))
    if n_mline:
        return Diagnosis("B-WALL-ENTITY", OK, "墙用多线（MLINE）绘制",
                         found=f"{n_mline} 条多线")
    kinds: dict[str, int] = {}
    for lay in wall_layers:
        for e in msp.query(f'*[layer=="{lay}"]'):
            kinds[e.dxftype()] = kinds.get(e.dxftype(), 0) + 1
    found = "、".join(f"{k} {v}" for k, v in kinds.items())
    return Diagnosis(
        "B-WALL-ENTITY", BLOCKER, "墙图层里有实体，但不是多线",
        found=found or "空",
        impact="双线画法的墙要先把平行线配对成墙带、再补门窗断口才能围合，"
               "这一步目前没做。强行按线段围合会把墙本身围成房间，面积全错。",
        fix="用 MLINE 命令画墙，并在多线样式里给出墙厚（外墙 200/300，内墙 100/200）。"
            "或者接受「这张图只能做表达类检查」。",
    )


#: 约定的墙图层名。墙分成「外墙」「内墙」是 GB/T 50001 §13.0.2 的分级命名，
#: 属正常做法，不是「分散」。
STANDARD_WALL_LAYERS = ("外墙", "内墙")


def _ck_layer_scatter(counts: dict[str, int]) -> Diagnosis:
    cands = [l for l in counts if WALL_NAME_RE.search(l)]
    odd = [l for l in cands if l not in STANDARD_WALL_LAYERS]
    if not odd:
        return Diagnosis(
            "A-LAYER-SCATTER", OK, "墙图层按约定分了内外墙",
            found="、".join(f"{l}({counts[l]})" for l in cands) or "无",
        )
    detail = "、".join(f"{l}({counts[l]})" for l in sorted(odd, key=lambda x: -counts[x]))
    return Diagnosis(
        "A-LAYER-SCATTER", ADVISORY, "墙图层没按「外墙/内墙」分",
        found=f"{len(odd)} 个非标准名：{detail}",
        impact="墙分散在自起名的图层上，围合时要跨层取线；换个人接手、换一张图之后，"
               "图层名对不上，几何识别就整个失效。",
        fix="墙统一放「外墙」和「内墙」两个图层，其余层的墙线并过去。",
    )


def _ck_layer_name(counts: dict[str, int]) -> Diagnosis:
    junk = sorted(l for l in counts if JUNK_NAME_RE.search(l))
    if not junk:
        return Diagnosis("A-LAYER-NAME", OK, "图层名没有机器生成痕迹")
    shown = "、".join(junk[:6]) + ("…" if len(junk) > 6 else "")
    return Diagnosis(
        "A-LAYER-NAME", ADVISORY, "存在无意义或机器生成的图层名",
        found=f"{len(junk)} 个：{shown}",
        impact="这类名字说明图是从别处拼来的（PDF 导入、他人图块），"
               "构件归类不可靠；也常伴随重复内容。",
        fix="PDF_ 开头的图层是导进来的底图，确认没用就删掉；"
            "纯数字层和 A$ 开头的匿名层把内容挪到语义图层后清空。",
    )


def _ck_layer_mix(counts: dict[str, int]) -> Diagnosis:
    cn = [l for l in counts if re.search(r"[\u4e00-\u9fa5]", l)]
    en = [l for l in counts if re.fullmatch(r"[A-Za-z0-9_\-.]+", l)]
    if not cn or not en:
        return Diagnosis("A-LAYER-MIX", OK, "图层命名体系统一",
                         found=f"中文 {len(cn)} 个、英文 {len(en)} 个")
    return Diagnosis(
        "A-LAYER-MIX", ADVISORY, "中文图层与英文图层并存",
        found=f"中文 {len(cn)} 个、英文 {len(en)} 个",
        impact="§13.0.1 第 3 款用「不应」，属强制口径。跨专业协同和程序按名检索都会出问题。",
        fix="整张图统一成一种命名体系。国内施工图习惯用中文，就全用中文。",
    )


#: 这几个图层如果是空的，多半不是「模板残留」而是「该画还没画」，值得点名。
KEY_LAYERS = ("楼梯", "门窗", "门", "窗", "家具", "轴线")


def _ck_empty_layers(doc, counts: dict[str, int]) -> Diagnosis:
    all_layers = [l.dxf.name for l in doc.layers]
    empty = [l for l in all_layers if l not in counts]
    if not all_layers:
        return Diagnosis("A-EMPTY-LAYER", OK, "没有图层")
    key_empty = [l for l in empty if l in KEY_LAYERS]
    ratio = len(empty) / len(all_layers)
    if ratio <= 0.5 and not key_empty:
        return Diagnosis("A-EMPTY-LAYER", OK, "空图层比例正常",
                         found=f"{len(empty)}/{len(all_layers)}")
    parts = [f"{len(empty)}/{len(all_layers)} 个图层在模型空间没有任何实体"]
    if key_empty:
        parts.append(f"其中 {'、'.join(key_empty)} 本来应该有内容")
    return Diagnosis(
        "A-EMPTY-LAYER", ADVISORY, "空图层过多",
        found="；".join(parts),
        impact="空图层可能是模板残留，也可能是「该画还没画」——"
               "空的「楼梯」层和空的「家具」层含义完全不同，工具分不出来，"
               "但审查结论里会少掉一整批判定。",
        fix="删掉模板残留的空层；如果空层是本该有内容的（比如楼梯），把内容补上。",
    )


def _ck_outliers(msp) -> Diagnosis:
    xs, ys = [], []
    for e in msp:
        try:
            pts = list(e.vertices() if e.dxftype() == "LWPOLYLINE" else [])
        except Exception:
            pts = []
        if not pts:
            t = e.dxftype()
            if t in ("LINE", "CIRCLE", "ARC"):
                try:
                    pts = [e.dxf.start, e.dxf.end] if t == "LINE" else [e.dxf.center]
                except Exception:
                    pts = []
            elif t == "POINT":
                try:
                    pts = [e.dxf.location]
                except Exception:
                    pts = []
            elif t in ("TEXT", "MTEXT"):
                try:
                    pts = [e.dxf.insert]
                except Exception:
                    pts = []
        for p in pts:
            xs.append(float(p[0]))
            ys.append(float(p[1]))
    if len(xs) < 20:
        return Diagnosis("A-OUTLIER", OK, "实体太少，不做离群判断")
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    px0, px1 = _percentile(xs, 0.01), _percentile(xs, 0.99)
    py0, py1 = _percentile(ys, 0.01), _percentile(ys, 0.99)
    span_x, span_y = max(px1 - px0, 1.0), max(py1 - py0, 1.0)
    out = max((px0 - x0) / span_x, (x1 - px1) / span_x,
              (py0 - y0) / span_y, (y1 - py1) / span_y)
    if out <= 0.2:
        return Diagnosis("A-OUTLIER", OK, "图形范围正常",
                         found=f"{x1 - x0:.0f} x {y1 - y0:.0f} mm")
    return Diagnosis(
        "A-OUTLIER", ADVISORY, "图形范围被少量离群实体撑大",
        found=f"实际范围 {x1 - x0:.0f} x {y1 - y0:.0f} mm，"
              f"1%~99% 分位只有 {px1 - px0:.0f} x {py1 - py0:.0f} mm",
        impact="按范围缩放会缩成一片空白；导出 PDF/图片时主体被压得很小。"
               "不是合规问题，但会让人以为图坏了。",
        fix="删掉远离图面的杂点，或把主体移回原点附近。",
    )


def _ck_scale(msp) -> Diagnosis:
    pat = re.compile(r"1\s*[:：∶]\s*\d+")
    for e in msp.query("TEXT MTEXT DIMENSION"):
        if e.dxftype() == "MTEXT":
            s = e.plain_text()
        elif e.dxftype() == "TEXT":
            s = e.dxf.get("text", "")
        else:
            s = e.dxf.get("text", "")
        if pat.search(s or ""):
            return Diagnosis("A-SCALE", OK, "图上有比例标注", found=f"命中「{s.strip()[:24]}」")
    return Diagnosis(
        "A-SCALE", ADVISORY, "图上没有比例标注",
        found="TEXT/MTEXT/DIMENSION 里都没找到 1:x 形式的文字",
        impact="审查时只能靠命令行 --scale 指定比例；指定错了，"
               "字高、线宽、轴号圆的判定会整体偏一档，而且报出来的都是「合规」。",
        fix="在图名下加一行比例文字，如「1:100」。",
    )


def _ck_axis(msp, counts: dict[str, int]) -> Diagnosis:
    axis_layers = [l for l in counts if re.search(r"轴|AXIS", l, re.I)]
    if axis_layers:
        return Diagnosis("A-AXIS", OK, "存在轴线图层", found="、".join(axis_layers[:4]))
    return Diagnosis(
        "A-AXIS", ADVISORY, "没有轴线图层",
        found="图层名里没有「轴」或 AXIS",
        impact="轴号圆直径、轴号字母、附加轴线形式这几条制图规则都判不了。",
        fix="把定位轴线放到「轴线」图层，轴号用圆加文字（字母 I/O/Z 不用）。",
    )


def diagnose(path) -> DoctorReport:
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    counts = _counts(msp)

    rep = DoctorReport(path=str(path))
    rep.items = [
        _ck_unit(doc),
        _ck_text(msp, doc),
        _ck_wall_layer(msp, counts),
        _ck_wall_entity(msp, counts),
        _ck_layer_scatter(counts),
        _ck_layer_mix(counts),
        _ck_layer_name(counts),
        _ck_empty_layers(doc, counts),
        _ck_axis(msp, counts),
        _ck_scale(msp),
        _ck_outliers(msp),
    ]
    return rep


# ── 渲染 ────────────────────────────────────────────────────────────────

def _w(s: str) -> int:
    """显示宽度，中文按两格算。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


#: 不能出现在行首的标点（避头尾）。中文排版里标点落在行首会显得像断句错误。
NO_LINE_HEAD = set("，。、；：！？）》」』】,.;:!?)")


def _wrap(text: str, width: int, indent: str) -> list[str]:
    out, line = [], ""
    for ch in text:
        if _w(line + ch) > width:
            if ch in NO_LINE_HEAD and len(line) > 1:
                # 把上一个字一起带到下一行，避免标点当行首
                out.append(indent + line[:-1])
                line = line[-1] + ch
            else:
                out.append(indent + line)
                line = ch
        else:
            line += ch
    if line:
        out.append(indent + line)
    return out


def _block(label: str, text: str, width: int, limit: int) -> list[str]:
    """一个带标签的段落：第一行带标签，续行用等宽空白对齐。"""
    pad = " " * _w(label)
    rows: list[str] = []
    for ln in _wrap(text, width - _w(label), ""):
        rows.append((label if not rows else pad) + ln)
    return rows[:limit]


def render_text(rep: DoctorReport, version: str = "0.1.0") -> str:
    W = 78
    lines: list[str] = []
    lines.append("=" * W)
    lines.append(f"  cadspec {version}  图纸可读性体检")
    lines.append("=" * W)
    lines.append(f"文件    {rep.path}")

    groups = [
        ("阻塞", BLOCKER, "✗", "不修就没法做尺度类检查"),
        ("建议", ADVISORY, "~", "不阻塞，但会让结论失真"),
        ("通过", OK, "✓", ""),
    ]
    for label, level, mark, hint in groups:
        items = [i for i in rep.items if i.level == level]
        if not items:
            continue
        head = f"── {label}  {len(items)} 项"
        if hint and level != OK:
            head += f"（{hint}）"
        lines.append("")
        lines.append(head + " " + "─" * max(2, W - _w(head) - 1))
        for i in items:
            lines.append(f"  {mark} [{i.code}] {i.title}")
            if i.ok:
                if i.found:
                    lines.append(f"      实际情况  {i.found}")
                continue
            if i.found:
                lines.extend(_block("      实际情况  ", i.found, W, 2))
            if i.impact:
                lines.extend(_block("      影响      ", i.impact, W, 3))
            if i.fix:
                lines.extend(_block("      改法      ", i.fix, W, 3))

    caps = rep.capabilities()
    lines.append("")
    lines.append("── 这张图能做什么 " + "─" * (W - 16))
    cap_rows = [
        ("图纸表达", caps["expression"], "图层、线宽、字高、比例"),
        ("几何识别", caps["geometry"], "墙体围合、房间多边形、门窗洞口"),
        ("房间尺度", caps["room"], "面积、短边、门宽分档"),
        ("尺寸标注", caps["dimension"], "数量与比例一致性"),
    ]
    for name, can, why in cap_rows:
        lines.append(f"  {_w_pad(name, 10)}{'能  ' if can else '不能'}{why}")

    lines.append("")
    if rep.blockers:
        codes = "、".join(i.code for i in rep.blockers)
        lines.append(f"  结论：{len(rep.blockers)} 项阻塞（{codes}）。")
        lines.append("  修完这些才能谈「尺度合不合规」；在那之前这张图只能判「表达规不规范」。")
    else:
        lines.append("  结论：没有阻塞项，几何与尺度类检查都可以跑。")
    if rep.advisories:
        lines.append(f"  另有 {len(rep.advisories)} 项建议，不修也能跑，但结论会失真。")
    return "\n".join(lines)


def _w_pad(s: str, n: int) -> str:
    return s + " " * max(0, n - _w(s))


def render_json(rep: DoctorReport) -> str:
    import json

    return json.dumps(
        {
            "path": rep.path,
            "blockers": len(rep.blockers),
            "advisories": len(rep.advisories),
            "capabilities": rep.capabilities(),
            "items": [
                {
                    "code": i.code,
                    "level": i.level,
                    "ok": i.ok,
                    "title": i.title,
                    "found": i.found,
                    "impact": i.impact,
                    "fix": i.fix,
                }
                for i in rep.items
            ],
        },
        ensure_ascii=False,
        indent=2,
    )

