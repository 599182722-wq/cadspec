"""门窗识别：判断"这个洞是门还是窗"的五条途径。

这些测试的重点不是门窗本身，而是**判断失败时工具怎么表现**。
洞口分不出门窗时，必须如实说 unknown，不能猜——猜错会让用户不再信任整份报告。
"""

from __future__ import annotations

import ezdxf
import pytest

from cadspec.geometry import extract
from cadspec.kb import load_rules
from cadspec.review import build_context
from cadspec.rules import run_rules


def _doc():
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.header["$LTSCALE"] = 1.0
    doc.header["$LIMMIN"] = (0.0, 0.0)
    doc.header["$LIMMAX"] = (420.0, 297.0)
    for name in ("外墙", "内墙", "门窗", "门", "窗", "标注"):
        doc.layers.add(name)
    return doc


def _mline_style(doc, name, offsets):
    from ezdxf.entities.mline import MLineStyleElement, MLineStyleElements

    els = MLineStyleElements()
    for off in offsets:
        els.elements.append(MLineStyleElement(off, 2, "BYLAYER"))
    st = doc.mline_styles.new(name)
    st.elements = els
    return st


def _opening_doc(tmp_path, layer="门窗", style="WINDOW", width=1200.0,
                 arc=False, code=None, wall=200):
    """一个 4.0×3.0m 的房间，底边留一个宽 width 的洞口，洞口处放一条门窗多线。"""
    doc = _doc()
    _mline_style(doc, "WALL", (0.5, -0.5))
    _mline_style(doc, "WINDOW", (0.5, 0.25, 0.0, -0.25))
    _mline_style(doc, "DOOR", (0.5, -0.5))
    msp = doc.modelspace()

    w, h = 4000.0, 3000.0
    x1 = 1400.0
    x2 = x1 + width
    attr = {"layer": "外墙", "style_name": "WALL", "scale_factor": wall}
    msp.add_mline([(0, 0), (x1, 0)], dxfattribs=attr)
    msp.add_mline([(x2, 0), (w, 0)], dxfattribs=attr)
    msp.add_mline([(w, 0), (w, h)], dxfattribs=attr)
    msp.add_mline([(w, h), (0, h)], dxfattribs=attr)
    msp.add_mline([(0, h), (0, 0)], dxfattribs=attr)

    msp.add_mline([(x1, 0), (x2, 0)], dxfattribs={
        "layer": layer, "style_name": style or "WALL", "scale_factor": wall,
    })
    if arc:
        msp.add_arc(center=(x1, 0), radius=width, start_angle=0, end_angle=90,
                    dxfattribs={"layer": layer})
    if code:
        msp.add_text(code, height=200, dxfattribs={"layer": layer}).set_placement(
            ((x1 + x2) / 2, 300))

    path = tmp_path / "opening.dxf"
    doc.saveas(path)
    return path


def _only_room_opening(path):
    geo = extract(path)
    real = [o for o in geo.openings if o.filled]
    assert len(real) == 1, f"期望 1 个洞口，实际 {len(real)}"
    return real[0]


# ---------------------------------------------------------------- 判定途径

def test_style_name_window(tmp_path):
    o = _only_room_opening(_opening_doc(tmp_path, style="WINDOW", width=1500))
    assert o.kind == "window"
    assert "WINDOW" in o.evidence


def test_style_name_door(tmp_path):
    o = _only_room_opening(_opening_doc(tmp_path, style="DOOR", width=900))
    assert o.kind == "door"


def test_narrow_window_style_is_contradiction(tmp_path):
    """样式说是窗，宽度却落在户门区间——不猜，报 unknown 并说明矛盾。"""
    o = _only_room_opening(_opening_doc(tmp_path, style="WINDOW", width=900))
    assert o.kind == "unknown"
    assert "矛盾" in o.evidence


def test_layer_name_door(tmp_path):
    o = _only_room_opening(_opening_doc(tmp_path, layer="门", style="DOOR", width=900))
    assert o.kind == "door"
    assert "只含门" in o.evidence


def test_merged_layer_name_is_not_evidence(tmp_path):
    """「门窗」两头都含，等于没说，不能据此定性。"""
    o = _only_room_opening(_opening_doc(tmp_path, layer="门窗", style="", width=1200))
    assert o.kind == "unknown"
    assert "无法区分" in o.evidence


def test_arc_means_door(tmp_path):
    o = _only_room_opening(_opening_doc(tmp_path, layer="门窗", style="", width=900,
                                        arc=True))
    assert o.kind == "door"
    assert "弧线" in o.evidence


def test_code_m_is_door(tmp_path):
    o = _only_room_opening(_opening_doc(tmp_path, layer="门窗", style="", width=900,
                                        code="M1"))
    assert o.kind == "door"
    assert "M1" in o.evidence


def test_code_c_is_window(tmp_path):
    o = _only_room_opening(_opening_doc(tmp_path, layer="门窗", style="", width=1500,
                                        code="C1"))
    assert o.kind == "window"
    assert "C1" in o.evidence


# ---------------------------------------------------------------- 规则

def _findings(path):
    ctx = build_context(path, scale=100, level="strict")
    ctx.openings = [o for o in ctx.openings if o["filled"]]
    return {rule.rule_id: fs for rule, fs in run_rules(ctx, load_rules())}


def test_unknown_opening_triggers_rule(tmp_path):
    path = _opening_doc(tmp_path, layer="门窗", style="", width=1200)
    found = _findings(path)
    assert found["R-OPEN-001"], "无法区分门窗时应报 warning"
    assert found["R-OPEN-001"][0].count == 1


def test_known_window_does_not_trigger_unknown_rule(tmp_path):
    path = _opening_doc(tmp_path, style="WINDOW", width=1500)
    found = _findings(path)
    assert found["R-OPEN-001"] == []


def test_door_too_narrow(tmp_path):
    path = _opening_doc(tmp_path, layer="门", style="DOOR", width=600)
    found = _findings(path)
    assert found["R-OPEN-002"], "600mm 门洞应命中"
    assert found["R-OPEN-002"][0].severity.value == "error"


def test_door_wide_enough(tmp_path):
    path = _opening_doc(tmp_path, layer="门", style="DOOR", width=900)
    found = _findings(path)
    assert found["R-OPEN-002"] == []


# ---------------------------------------------------------------- 真实样例

def test_reused_style_is_demoted():
    """同层同一样式画了 900 和 2400 两种洞口——这个样式名不可能是类型依据。"""
    from cadspec.geometry import Opening, _demote_reused_styles

    ops = [
        Opening(layer="门窗", width=w, start=(0, 0), end=(w, 0), filled=True,
                kind="window", evidence="多线样式「WINDOW」", style="WINDOW")
        for w in (900, 1200, 1800, 2400)
    ]
    _demote_reused_styles(ops)
    assert all(o.kind == "unknown" for o in ops)
    assert "复用" in ops[0].evidence


def test_consistent_style_is_kept():
    """宽度都落在窗的区间内，样式名可信，不降级。"""
    from cadspec.geometry import Opening, _demote_reused_styles

    ops = [
        Opening(layer="门窗", width=w, start=(0, 0), end=(w, 0), filled=True,
                kind="window", evidence="多线样式「WINDOW」", style="WINDOW")
        for w in (1500, 1600, 1700, 1800)
    ]
    _demote_reused_styles(ops)
    assert all(o.kind == "window" for o in ops)


def _door_arc_doc(tmp_path, width=900.0, radius=None):
    """按标准画法画一条门：洞口一端是铰点，门扇弧扫开 45 度。"""
    import math

    doc = _doc()
    msp = doc.modelspace()
    r = radius if radius is not None else width
    # 洞口 (0,0)-(width,0)，铰点在 (width,0)，弧从 (0,0) 扫到 45 度处
    ang = math.radians(45)
    p1 = (width - r * math.cos(ang), r * math.sin(ang))
    msp.add_lwpolyline(
        [(0.0, 0.0, 0.2), p1, (width, 0.0)],
        format="xyb", dxfattribs={"layer": "门窗"},
    )
    path = tmp_path / "door.dxf"
    doc.saveas(path)
    return path


def test_door_arc_is_recognized(tmp_path):
    from cadspec.geometry import _door_arcs

    doc = ezdxf.readfile(_door_arc_doc(tmp_path, width=900))
    arcs = _door_arcs(doc.modelspace())
    assert len(arcs) == 1
    assert abs(arcs[0].width - 900) < 1
    assert arcs[0].kind == "door"


def test_arc_with_mismatched_radius_is_ignored(tmp_path):
    """半径和洞口宽对不上，就不是门扇弧，可能是家具圆角。"""
    from cadspec.geometry import _door_arcs

    doc = ezdxf.readfile(_door_arc_doc(tmp_path, width=900, radius=500))
    assert _door_arcs(doc.modelspace()) == []


@pytest.mark.skipif(not __import__("pathlib").Path(r"D:\dsh\cad\1.dxf").exists(),
                    reason="样例图不在")
def test_real_sample_openings():
    geo = extract(r"D:\dsh\cad\1.dxf")
    real = [o for o in geo.openings if o.filled]
    # 11 个门靠门扇弧认出来，12 个洞口没有弧、按窗计
    assert len(real) == 23
    kinds = [o.kind for o in real]
    assert kinds.count("door") == 11
    assert kinds.count("window") == 12
    # 门的宽度都在 700~1000，是室内门的正常范围
    doors = [o for o in real if o.kind == "door"]
    assert min(o.width for o in doors) >= 700
    assert max(o.width for o in doors) <= 1001
