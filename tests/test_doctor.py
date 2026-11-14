# -*- coding: utf-8 -*-
"""图纸可读性体检（doctor）的回归测试。

这套检查的价值不在「发现问题」，而在「不把读不懂当成没问题」——
所以每条阻塞项都要有一条测试钉住，防止哪天被顺手放宽。
"""
from __future__ import annotations

import json

import ezdxf

from cadspec import doctor


def _mline_style(doc, name, offsets):
    from ezdxf.entities.mline import MLineStyleElement, MLineStyleElements

    els = MLineStyleElements()
    for off in offsets:
        els.elements.append(MLineStyleElement(off, 2, "BYLAYER"))
    st = doc.mline_styles.new(name)
    st.elements = els
    return st


def _base(unit: int = 4, mline: bool = True, text: str | None = "卧室"):
    doc = ezdxf.new("R2010", setup=False)
    doc.header["$INSUNITS"] = unit
    for name, color in [("外墙", 2), ("内墙", 2), ("门窗", 3), ("标注", 3), ("轴线", 1)]:
        doc.layers.add(name, color=color)
    msp = doc.modelspace()
    if mline:
        _mline_style(doc, "WALL", (0.5, -0.5))
        msp.add_mline(
            [(0, 0), (6000, 0)],
            dxfattribs={"layer": "外墙", "style_name": "WALL", "scale_factor": 200},
        )
    else:
        msp.add_line((0, 0), (6000, 0), dxfattribs={"layer": "外墙"})
    if text:
        msp.add_text(text, height=200, dxfattribs={"layer": "标注"}).set_placement((3000, 2000))
    return doc


def _run(tmp_path, doc) -> doctor.DoctorReport:
    path = tmp_path / "t.dxf"
    doc.saveas(path)
    return doctor.diagnose(path)


def _by_code(rep: doctor.DoctorReport, code: str) -> doctor.Diagnosis:
    for item in rep.items:
        if item.code == code:
            return item
    raise AssertionError(f"没有 {code} 这一项")


# ---------------------------------------------------------------- 正常图

def test_clean_drawing_has_no_blockers(tmp_path):
    rep = _run(tmp_path, _base())
    assert rep.blockers == [], [i.code for i in rep.blockers]
    caps = rep.capabilities()
    assert caps["geometry"] and caps["room"] and caps["dimension"]


# ---------------------------------------------------------------- 阻塞项

def test_missing_unit_blocks_dimension_checks(tmp_path):
    rep = _run(tmp_path, _base(unit=0))
    assert _by_code(rep, "B-UNIT").level == doctor.BLOCKER
    assert rep.capabilities()["dimension"] is False


def test_no_text_blocks_room_checks(tmp_path):
    rep = _run(tmp_path, _base(text=None))
    assert _by_code(rep, "B-TEXT").level == doctor.BLOCKER
    assert rep.capabilities()["room"] is False
    # 没有文字不影响几何：墙照样围得出来
    assert rep.capabilities()["geometry"] is True


def test_line_walls_block_geometry(tmp_path):
    """双线墙没做配对，按线段围合会把墙本身围成房间，所以必须先挡住。"""
    rep = _run(tmp_path, _base(mline=False))
    item = _by_code(rep, "B-WALL-ENTITY")
    assert item.level == doctor.BLOCKER
    assert "LINE" in item.found
    assert rep.capabilities()["geometry"] is False
    assert rep.capabilities()["room"] is False


def test_no_wall_layer_at_all(tmp_path):
    doc = ezdxf.new("R2010", setup=False)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("FRAME", color=2)
    msp = doc.modelspace()
    msp.add_line((0, 0), (6000, 0), dxfattribs={"layer": "FRAME"})
    msp.add_text("卧室", height=200, dxfattribs={"layer": "FRAME"}).set_placement((3000, 2000))
    rep = _run(tmp_path, doc)
    item = _by_code(rep, "B-WALL-LAYER")
    assert item.level == doctor.BLOCKER
    assert rep.capabilities()["geometry"] is False


def test_qing_layer_name_is_recognised(tmp_path):
    """天正常用 QT-* 表示墙体，也应认出来。"""
    doc = _base()
    doc.layers.add("QT-AREA", color=2)
    for e in doc.modelspace():
        e.dxf.layer = "QT-AREA"
    rep = _run(tmp_path, doc)
    assert _by_code(rep, "B-WALL-LAYER").ok


# ---------------------------------------------------------------- 建议项

def test_wall_layers_outside_convention_are_advisory(tmp_path):
    doc = _base()
    doc.layers.add("WALL2", color=2)
    doc.modelspace().add_line((0, 5000), (6000, 5000), dxfattribs={"layer": "WALL2"})
    rep = _run(tmp_path, doc)
    assert _by_code(rep, "A-LAYER-SCATTER").level == doctor.ADVISORY
    assert "WALL2" in _by_code(rep, "A-LAYER-SCATTER").found


def test_outer_and_inner_wall_split_is_not_scatter(tmp_path):
    """「外墙」「内墙」是 §13.0.2 的分级命名，不能报成图层分散。"""
    rep = _run(tmp_path, _base())
    assert _by_code(rep, "A-LAYER-SCATTER").ok


def test_mixed_scripts_are_advisory(tmp_path):
    doc = _base()
    doc.layers.add("WALL", color=2)
    doc.modelspace().add_line((0, 7000), (6000, 7000), dxfattribs={"layer": "WALL"})
    rep = _run(tmp_path, doc)
    assert _by_code(rep, "A-LAYER-MIX").level == doctor.ADVISORY


def test_scale_label_is_recognised(tmp_path):
    doc = _base()
    doc.modelspace().add_text("1:100", height=200, dxfattribs={"layer": "标注"})
    rep = _run(tmp_path, doc)
    assert _by_code(rep, "A-SCALE").ok


def test_missing_scale_is_advisory(tmp_path):
    rep = _run(tmp_path, _base())
    assert _by_code(rep, "A-SCALE").level == doctor.ADVISORY


def test_empty_key_layer_is_named(tmp_path):
    """空的「楼梯」层不是模板残留，多半是还没画。"""
    doc = _base()
    doc.layers.add("楼梯", color=4)
    rep = _run(tmp_path, doc)
    item = _by_code(rep, "A-EMPTY-LAYER")
    assert item.level == doctor.ADVISORY
    assert "楼梯" in item.found


# ---------------------------------------------------------------- 细节

def test_anonymous_block_text_does_not_count(tmp_path):
    """尺寸标注生成的 *D 块里有文字，那是标注值，不是图上的内容。"""
    doc = _base(text=None)
    blk = doc.blocks.new("*D99")
    blk.add_text("2375", height=200)
    rep = _run(tmp_path, doc)
    assert _by_code(rep, "B-TEXT").level == doctor.BLOCKER
    assert "块定义内 0 个" in _by_code(rep, "B-TEXT").found


def test_named_block_text_is_reported(tmp_path):
    doc = _base(text=None)
    blk = doc.blocks.new("家具")
    blk.add_text("冰箱", height=200)
    doc.modelspace().add_blockref("家具", (0, 0))
    rep = _run(tmp_path, doc)
    item = _by_code(rep, "B-TEXT")
    assert item.level == doctor.BLOCKER
    assert "冰箱" in item.found


def test_outlier_entity_is_detected(tmp_path):
    doc = _base()
    msp = doc.modelspace()
    # 一堆正常图元 + 一个远在 50 米外的杂点
    for i in range(40):
        msp.add_line((i * 100, 0), (i * 100, 4000))
    msp.add_point((50000, 50000))
    rep = _run(tmp_path, doc)
    assert _by_code(rep, "A-OUTLIER").level == doctor.ADVISORY


def test_json_output_is_parsable(tmp_path):
    rep = _run(tmp_path, _base(unit=0, text=None))
    data = json.loads(doctor.render_json(rep))
    assert data["blockers"] >= 2
    assert data["capabilities"]["dimension"] is False
    assert {i["code"] for i in data["items"]} >= {"B-UNIT", "B-TEXT"}


def test_text_output_mentions_capability(tmp_path):
    rep = _run(tmp_path, _base(mline=False))
    text = doctor.render_text(rep)
    assert "几何识别" in text
    assert "不能" in text
    assert "阻塞" in text
