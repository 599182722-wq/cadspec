# -*- coding: utf-8 -*-
"""从 DXF 提取规则要吃的全部事实。

原则：这里不做任何判定，只做事实抽取。判定全部留给 rules.py。
这样同一份 ReviewContext 可以被反复用来调规则、写新规则、做回归测试。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import ezdxf

from .model import ReviewContext

# 轴号文本的形态：1 / A / AA / A1 / 1/D / D/1
AXIS_LABEL_RE = re.compile(r"^(?:\d{1,3}|[A-Z]{1,2}|[A-Z]\d{1,2}|\d{1,3}/[A-Z]|[A-Z]/\d{1,3})$")
# 轴号所在图层的常见关键词（中文设计院主流 + 英文模板）
AXIS_LAYER_HINT = re.compile(r"轴|标注|dim|axis|anno", re.IGNORECASE)
# 纯字母数字文本（用于字高判定；不含汉字）
ALNUM_TEXT_RE = re.compile(r"^[0-9A-Za-z.+%\-\s±]*$")


def _hdr(doc, key, default):
    try:
        val = doc.header.get(key, default)
    except Exception:
        return default
    return default if val is None else val


# 比例标注的形态：1:100 / 1：50 / 1 : 200
SCALE_TEXT_RE = re.compile(r"1\s*[:：∶]\s*\d{2,4}")
# 标高文字的形态：5.600 / ±0.000 / -1.200 / +3.300
ELEV_TEXT_RE = re.compile(r"^[±+\-]?\d{1,2}\.\d{2,3}$")


def _layer_total(layer_counts: dict[str, dict[str, int]], name: str) -> int:
    return sum(layer_counts.get(name, {}).values())


def _completeness(
    rooms: list[dict],
    openings: list[dict],
    layers: list[dict],
    layer_counts: dict[str, dict[str, int]],
    dim_count: int,
    texts: list[dict],
    level: str,
) -> dict:
    """图面完整性清单。

    为什么要有这个：规则只验「画出来的东西对不对」，验不了「该画的有没有画」。
    一张只画了一半的图跑完所有规则可能全绿，而全绿比报错更危险。
    这个清单不判合规，只回答一件事——**这张图现在能被审到什么程度**。
    """
    items: list[dict] = []

    def add(key, label, ok, detail, unlocks=""):
        items.append({
            "key": key, "label": label, "ok": ok,
            "detail": detail, "unlocks": unlocks,
        })

    # 1 房间命名
    if level != "strict":
        add("room_label", "房间名称", None,
            "未启用几何识别（需要 --level strict）", "房间尺度类规则")
    elif not rooms:
        add("room_label", "房间名称", False, "没有识别到任何房间",
            "房间尺度类规则（卧室/厨房/卫生间面积、短边、门宽分档）")
    else:
        named = sum(1 for r in rooms if r.get("label"))
        add("room_label", "房间名称", named / len(rooms) >= 0.8,
            f"{len(rooms)} 个房间里 {named} 个有名字",
            "房间尺度类规则（卧室/厨房/卫生间面积、短边、门宽分档）")

    # 2 门窗可分辨
    doors = [o for o in openings if o.get("kind") == "door" and o.get("filled")]
    wins = [o for o in openings if o.get("kind") == "window" and o.get("filled")]
    unknown = [o for o in openings if o.get("kind") == "unknown" and o.get("filled")]
    if level != "strict":
        add("opening_kind", "门窗类型", None, "未启用几何识别", "门宽分档")
    else:
        add("opening_kind", "门窗类型", not unknown,
            f"门 {len(doors)} 处、窗 {len(wins)} 处、未区分 {len(unknown)} 处",
            "门宽分档（户门 0.90 / 卧室门 0.80 / 厨卫门 0.70）")

    # 3 楼梯
    stair_layers = [l["name"] for l in layers if "梯" in (l.get("name") or "")]
    if not stair_layers:
        add("stairs", "楼梯", None, "没有名为「楼梯」的图层，无从判断",
            "楼梯踏步、梯段净宽")
    else:
        cnt = sum(_layer_total(layer_counts, n) for n in stair_layers)
        add("stairs", "楼梯", cnt > 0,
            f"图层 {'、'.join(stair_layers)} 共 {cnt} 个实体",
            "楼梯踏步、梯段净宽")

    # 4 尺寸标注
    add("dimensions", "尺寸标注", dim_count >= 3,
        f"DIMENSION 实体 {dim_count} 个",
        "三道尺寸（总尺寸/轴线间/门窗洞口）")

    # 5 比例标注
    has_scale = any(SCALE_TEXT_RE.search(str(t.get("text") or "")) for t in texts)
    add("scale", "比例标注", has_scale,
        "找到 1:N 比例文字" if has_scale else "图上没有比例标注",
        "比例一致性校验")

    # 6 剖切符号 / 指北针
    section_layers = [l["name"] for l in layers if "剖切" in (l.get("name") or "")]
    section_cnt = sum(_layer_total(layer_counts, n) for n in section_layers)
    has_north = any("北" in str(t.get("text") or "") for t in texts)
    add("orientation", "剖切符号与指北针", section_cnt > 0 or has_north,
        (f"剖切符号 {section_cnt} 个；" if section_layers else "无剖切符号图层；")
        + ("有方位标记" if has_north else "无指北针/风玫瑰"),
        "SJD-2016 4.3.4-13")

    # 7 标高
    elev = [t for t in texts if ELEV_TEXT_RE.match(str(t.get("text") or "").strip())]
    add("levels", "标高", len(elev) >= 1,
        f"{len(elev)} 处标高文字" + ("（缺室外地坪标高需人工核对）" if elev else ""),
        "SJD-2016 4.3.4-12")

    ok = sum(1 for i in items if i["ok"] is True)
    applicable = sum(1 for i in items if i["ok"] is not None)
    return {"items": items, "ok": ok, "applicable": applicable}



def _plain_text(entity) -> str:
    """TEXT 取 dxf.text；MTEXT 优先 plain_text()，退回原始串去掉格式码。"""
    if entity.dxftype() == "TEXT":
        return (entity.dxf.get("text", "") or "").strip()
    try:
        return entity.plain_text().strip()
    except Exception:
        raw = entity.dxf.get("text", "") or ""
        return re.sub(r"\\[A-Za-z][^;]*;", "", raw).strip()


def _text_height(entity) -> float:
    if entity.dxftype() == "TEXT":
        return float(entity.dxf.get("height", 0.0) or 0.0)
    return float(entity.dxf.get("char_height", 0.0) or 0.0)


def build_context(path: str | Path, scale: int = 100, level: str = "basic") -> ReviewContext:
    path = Path(path)
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()

    layers: list[dict] = []
    for lyr in doc.layers:
        layers.append({
            "name": lyr.dxf.name,
            "color": lyr.dxf.get("color", 7),
            "linetype": lyr.dxf.get("linetype", "Continuous"),
            "lineweight": lyr.dxf.get("lineweight", -3),
            "plot": int(lyr.dxf.get("plot", 1)),
            "off": bool(lyr.is_off()),
            "frozen": bool(lyr.is_frozen()),
        })

    texts: list[dict] = []
    circles: list[dict] = []
    mlines: list[dict] = []
    entity_counts: Counter[str] = Counter()
    layer_counts: dict[str, Counter[str]] = defaultdict(Counter)
    layer_names = {l["name"] for l in layers}

    for e in msp:
        etype = e.dxftype()
        lname = e.dxf.get("layer", "0")
        entity_counts[etype] += 1
        layer_counts[lname][etype] += 1

        if etype in ("TEXT", "MTEXT"):
            content = _plain_text(e)
            h = _text_height(e)
            pos = e.dxf.get("insert") if etype == "TEXT" else e.dxf.get("insert")
            x = float(pos[0]) if pos is not None else 0.0
            y = float(pos[1]) if pos is not None else 0.0
            texts.append({
                "handle": e.dxf.handle,
                "type": etype,
                "layer": lname,
                "text": content,
                "height": h,
                "height_printed": h / scale if scale else h,
                "x": x,
                "y": y,
                "is_axis_label": bool(
                    AXIS_LABEL_RE.match(content) and AXIS_LAYER_HINT.search(lname)
                ),
                "is_alnum": bool(ALNUM_TEXT_RE.match(content)) and any(c.isalnum() for c in content),
            })
        elif etype == "CIRCLE":
            c = e.dxf.center
            circles.append({
                "handle": e.dxf.handle,
                "layer": lname,
                "radius": float(e.dxf.radius),
                "cx": float(c[0]),
                "cy": float(c[1]),
            })
        elif etype == "MLINE":
            mlines.append({
                "handle": e.dxf.handle,
                "layer": lname,
                "style": e.dxf.get("style_name", "Standard"),
                "scale_factor": float(e.dxf.get("scale_factor", 1.0) or 1.0),
            })

    # 轴号文本 -> 最近的圆，作为"轴号圆圈"候选
    axis_texts = [t for t in texts if t["is_axis_label"]]
    for t in axis_texts:
        best, best_d = None, None
        for c in circles:
            if c["layer"] != t["layer"] and c["layer"] not in layer_names:
                continue
            d = ((c["cx"] - t["x"]) ** 2 + (c["cy"] - t["y"]) ** 2) ** 0.5
            if best_d is None or d < best_d:
                best, best_d = c, d
        t["circle_handle"] = best["handle"] if best else None
        t["circle_radius"] = best["radius"] if best else None
        t["circle_distance"] = best_d

    extmin = _hdr(doc, "$EXTMIN", None)
    extmax = _hdr(doc, "$EXTMAX", None)
    if extmin is not None and extmax is not None:
        extents = (
            (float(extmin[0]), float(extmin[1])),
            (float(extmax[0]), float(extmax[1])),
        )
    else:
        extents = None

    rooms: list[dict] = []
    openings: list[dict] = []
    geometry_note: str | None = None
    if level == "strict":
        try:
            from .geometry import extract
            from .rooms import annotate

            geo = extract(path)
            annotate(path, geo)
            rooms = [r.as_dict() for r in geo.rooms]
            openings = [o.as_dict() for o in geo.openings]
            if not rooms:
                geometry_note = (
                    "未识别到房间：墙线可能不是 MLINE，或图层名不是「外墙」「内墙」。"
                    "此档的尺寸类条文未参与判定。"
                )
        except Exception as exc:  # 几何识别失败不能让整次检查挂掉
            geometry_note = f"几何识别失败（{exc}），尺寸类条文未参与判定。"

    limmin = _hdr(doc, "$LIMMIN", (0.0, 0.0))
    limmax = _hdr(doc, "$LIMMAX", (420.0, 297.0))

    return ReviewContext(
        path=str(path),
        dxfversion=doc.dxfversion,
        insunits=int(_hdr(doc, "$INSUNITS", 0)),
        ltscale=float(_hdr(doc, "$LTSCALE", 1.0)),
        limmin=(float(limmin[0]), float(limmin[1])),
        limmax=(float(limmax[0]), float(limmax[1])),
        scale=scale,
        layers=layers,
        texts=texts,
        circles=circles,
        mlines=mlines,
        entity_counts=dict(entity_counts),
        layer_entity_counts={k: dict(v) for k, v in layer_counts.items()},
        dim_count=entity_counts.get("DIMENSION", 0),
        block_names=[b.name for b in doc.blocks if not b.name.startswith("*")],
        layout_names=[l.name for l in doc.layouts],
        extents=extents,
        rooms=rooms,
        openings=openings,
        geometry_level=level,
        geometry_note=geometry_note,
        completeness=_completeness(
            rooms, openings, layers, layer_counts,
            entity_counts.get("DIMENSION", 0), texts, level,
        ),
    )
