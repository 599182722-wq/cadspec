# -*- coding: utf-8 -*-
"""回归测试：知识库自洽性 + 几条关键规则在合成图纸上的行为。

不依赖仓库外的任何 DXF —— 每个用例用 ezdxf 现场造图，
这样 CI 上也能跑，也不会把用户的真实图纸带进仓库。
"""
from __future__ import annotations

import re

import ezdxf
import pytest

from cadspec.kb import load_clauses, load_rules, validate_kb
from cadspec.review import build_context
from cadspec.rules import run_rules


# ---------------------------------------------------------------- 知识库

def test_kb_is_self_consistent():
    clauses = load_clauses()
    rules = load_rules()
    assert clauses, "知识库为空"
    assert rules, "规则为空"
    assert validate_kb(clauses, rules) == []


def test_every_rule_has_a_title_and_severity():
    for rule in load_rules():
        assert rule.title.strip()
        assert rule.severity.value in {"error", "warning", "info"}


def test_severity_of_cite_matches_wording():
    """「应/不应/必须/不得」不该出现 warning，「宜」不该出现 error。

    这是本项目最容易退化的地方：条文换了版本、规则跟着改了级别，
    但没人回头核对用词。这条测试就是那道刹车。
    """
    clauses = load_clauses()
    hard = ("应", "不应", "必须", "不得")
    soft = ("宜", "不宜")
    for clause in clauses.values():
        text = clause.verbatim
        if any(clause.clause_id.endswith(s) for s in ("preface",)):
            continue
        has_hard = any(w in text for w in hard)
        has_soft = any(w in text for w in soft)
        if has_hard and not has_soft:
            assert clause.severity_hint.value == "error", (
                f"{clause.clause_id} 用了强制性用词，severity_hint 应为 error"
            )


# ---------------------------------------------------------------- 规则

def _doc():
    doc = ezdxf.new("R2010", setup=False)
    doc.header["$INSUNITS"] = 4
    for name, color in [
        ("轴线", 1), ("外墙", 2), ("内墙", 2), ("门窗", 3), ("标注", 3), ("高差线", 6),
    ]:
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    return doc


def _findings(tmp_path, doc, scale=100) -> dict[str, list]:
    path = tmp_path / "case.dxf"
    doc.saveas(path)
    ctx = build_context(path, scale=scale)
    return {rule.rule_id: fs for rule, fs in run_rules(ctx, load_rules())}


def test_uniform_chinese_layers_pass(tmp_path):
    """全中文图层不应触发汉英混用。"""
    doc = _doc()
    found = _findings(tmp_path, doc)
    assert found["R-LAYER-001"] == []


def test_mixed_scripts_are_an_error(tmp_path):
    """中文层与英文层并存 -> §13.0.1 第 3 款（不应）-> error。"""
    doc = _doc()
    doc.layers.add("WALL")
    found = _findings(tmp_path, doc)
    assert found["R-LAYER-001"]
    assert found["R-LAYER-001"][0].severity.value == "error"


def test_single_field_layer_names_are_only_a_warning(tmp_path):
    """§13.0.2 全为「宜」，单字段中文层名只能给 warning。"""
    doc = _doc()
    found = _findings(tmp_path, doc)
    assert found["R-LAYER-002"]
    assert found["R-LAYER-002"][0].severity.value == "warning"


def test_text_height_is_measured_on_the_printed_sheet(tmp_path):
    """1:100 下 200 高的模型字高 = 2.0mm 打印字高 < 2.5mm。"""
    doc = _doc()
    doc.modelspace().add_text("5.600", height=200, dxfattribs={"layer": "高差线"})
    found = _findings(tmp_path, doc, scale=100)
    assert found["R-TEXT-001"], "应当命中 2.0mm < 2.5mm"
    assert found["R-TEXT-001"][0].severity.value == "error"

    # 同样这张图按 1:50 出图，2.0mm 变成 4.0mm，就不该报了
    found_50 = _findings(tmp_path, doc, scale=50)
    assert found_50["R-TEXT-001"] == []


def test_forbidden_axis_letter_is_an_error(tmp_path):
    """§8.0.4 用「不得」，I/O/Z 作轴号必须报 error。"""
    doc = _doc()
    doc.modelspace().add_text("I", height=400, dxfattribs={"layer": "标注"})
    found = _findings(tmp_path, doc)
    assert found["R-AXIS-002"]
    assert found["R-AXIS-002"][0].severity.value == "error"


def test_legal_axis_letters_pass(tmp_path):
    """跳过 I 是正确做法，不该被误报。"""
    doc = _doc()
    msp = doc.modelspace()
    for i, label in enumerate(["A", "B", "C", "H", "J"]):
        msp.add_text(label, height=400, dxfattribs={"layer": "标注", "insert": (0, i * 3000)})
    found = _findings(tmp_path, doc)
    assert found["R-AXIS-002"] == []
    assert found["R-AXIS-004"] == []


def test_slash_axis_label_is_a_warning(tmp_path):
    """「1/D」能读懂但不合 §8.0.6 的分数形式。"""
    doc = _doc()
    doc.modelspace().add_text("1/D", height=400, dxfattribs={"layer": "标注"})
    found = _findings(tmp_path, doc)
    assert found["R-AXIS-003"]
    assert found["R-AXIS-003"][0].severity.value == "warning"


def test_axis_circle_diameter_uses_scale(tmp_path):
    """轴号圆：模型半径 400 -> 1:100 时直径 8.0mm，合规。"""
    doc = _doc()
    msp = doc.modelspace()
    msp.add_circle((0, 0), radius=400, dxfattribs={"layer": "标注"})
    msp.add_text("1", height=400, dxfattribs={"layer": "标注", "insert": (0, 0)})
    assert _findings(tmp_path, doc, scale=100)["R-AXIS-001"] == []

    # 半径 250 -> 直径 5.0mm，小于 8mm，应命中
    doc2 = _doc()
    msp2 = doc2.modelspace()
    msp2.add_circle((0, 0), radius=250, dxfattribs={"layer": "标注"})
    msp2.add_text("1", height=400, dxfattribs={"layer": "标注", "insert": (0, 0)})
    assert _findings(tmp_path, doc2, scale=100)["R-AXIS-001"]


def test_unset_lineweight_is_flagged(tmp_path):
    doc = _doc()
    found = _findings(tmp_path, doc)
    assert found["R-LW-001"]
    assert found["R-LW-001"][0].severity.value == "warning"


@pytest.mark.parametrize("units", [4, 0])
def test_metric_units_pass(tmp_path, units):
    doc = _doc()
    doc.header["$INSUNITS"] = units
    assert _findings(tmp_path, doc)["R-UNIT-001"] == []


def test_inch_units_are_an_error(tmp_path):
    doc = _doc()
    doc.header["$INSUNITS"] = 1
    found = _findings(tmp_path, doc)
    assert found["R-UNIT-001"]
    assert found["R-UNIT-001"][0].severity.value == "error"


# ---------------------------------------------------------------- 深度规定
#
# 这一组守的是"依据来源"：R-DIM-001/002 的依据是《建筑工程设计文件编制深度规定》，
# 不是 GB/T 50001。如果哪天有人把 cite 改回 null 或指向 50001，说明知识库被
# 拼错了——深度规定管"该有的内容有没有"，50001 管"画得对不对"，两者不能互相顶替。

def test_depth_regulation_clauses_are_loaded():
    clauses = load_clauses()
    for cid in ("SJD-2016/4.3.4-1", "SJD-2016/4.3.4-20"):
        assert cid in clauses, f"{cid} 未录入"
        assert clauses[cid].verbatim.strip(), f"{cid} 缺少条文原文"


def test_dim_rules_cite_the_depth_regulation():
    rules = {r.rule_id: r for r in load_rules()}
    assert rules["R-DIM-001"].cite == "SJD-2016/4.3.4-1"
    assert rules["R-DIM-001"].severity.value == "error"
    assert rules["R-DIM-002"].cite == "SJD-2016/4.3.4-20"


def test_no_dimension_entity_is_an_error(tmp_path):
    """DIMENSION 为 0 -> 深度规定 4.3.4-1 -> error（不再是当初的 info）。"""
    doc = _doc()
    found = _findings(tmp_path, doc)
    assert found["R-DIM-001"], "没有尺寸标注时应当命中"
    assert found["R-DIM-001"][0].severity.value == "error"
    assert found["R-DIM-001"][0].cite == "SJD-2016/4.3.4-1"


def test_drawing_with_scale_annotation_passes(tmp_path):
    doc = _doc()
    doc.modelspace().add_text("1:100", height=500, dxfattribs={"layer": "标注"})
    assert _findings(tmp_path, doc)["R-DIM-002"] == []


def test_drawing_without_scale_annotation_is_flagged(tmp_path):
    doc = _doc()
    found = _findings(tmp_path, doc)
    assert found["R-DIM-002"]
    assert found["R-DIM-002"][0].severity.value == "warning"


def test_rule_severity_comes_from_yaml_not_from_the_check_function(tmp_path):
    """级别以 data/rules 为准：改 YAML 就能调级，函数里的 severity 只是默认值。"""
    from cadspec.model import Severity

    doc = _doc()
    path = tmp_path / "case.dxf"
    doc.saveas(path)
    ctx = build_context(path, scale=100)

    rule = next(r for r in load_rules() if r.rule_id == "R-DIM-001")
    rule.severity = Severity.WARNING
    found = {r.rule_id: fs for r, fs in run_rules(ctx, [rule])}
    assert found["R-DIM-001"][0].severity.value == "warning"


# ---------------------------------------------------------------- GB 55037
#
# 这一组守的是「机器录入」的正确性。GB 55037 的 238 条是用
# scripts/ingest_pdf.py 从 PDF 切的，不是手抄的，所以要防三类问题：
#   1. clause_id 丢了标准前缀（裸 "1.0.1" 会和别的规范撞车）
#   2. 公告里的废止清单被当成条文（曾把一条清单切成 3.2.1）
#   3. PDF 页眉/页码混进正文

def _gb55037() -> list:
    return [c for c in load_clauses().values() if c.standard == "GB55037-2022"]


def test_gb55037_is_loaded():
    clauses = _gb55037()
    assert len(clauses) >= 200, f"只录到 {len(clauses)} 条，切分可能漏了"
    for c in clauses:
        assert c.verbatim.strip(), f"{c.clause_id} 缺原文"


def test_gb55037_ids_are_prefixed():
    """每条 id 都要带标准号。裸 id 会让 SJD-2016/4.3.4-1 和别的 4.3.4-1 混淆。"""
    for c in _gb55037():
        assert c.clause_id.startswith("GB55037-2022/"), c.clause_id
        bare = c.clause_id.split("/", 1)[1]
        assert re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{1,2}", bare), c.clause_id


def test_gb55037_body_has_no_watermark_or_toc_lines():
    needle = ("1190119", "消防大讲堂", "消防资源网", "……", "....")
    for c in _gb55037():
        for bad in needle:
            assert bad not in c.verbatim, f"{c.clause_id} 混入了 {bad!r}"


def test_gb55037_has_no_abolition_list_leak():
    """公告中「同时废止下列标准相关强制性条文」的清单不能出现在条文里。"""
    for c in _gb55037():
        body = c.verbatim
        assert "同时废止" not in body, c.clause_id
        assert not re.search(r"GB\s?\d{5}-\d{4}\s*第", body), f"{c.clause_id} 像是废止清单"


def test_gb55037_every_clause_has_chapter():
    for c in _gb55037():
        assert c.chapter.strip(), f"{c.clause_id} 没有归章，切分时丢了章标题"


def test_gb55037_severity_follows_wording():
    """用词含「应/不应/必须/不得」的条文，severity_hint 必须是 error。"""
    must = re.compile(r"(?<![相响适供对反])应|必须|不得|严禁|禁止")
    for c in _gb55037():
        if must.search(c.verbatim):
            assert c.severity_hint.value == "error", c.clause_id


# ---------------------------------------------------------------- GB 55031
#
# 这本是从 HTML 抓的（官方 PDF 是扫描件）。HTML 版把正文和条文说明放在
# 同一个容器里、二者都用条文号开头，所以要防「条文说明被当成正文」：
# 说明文字几乎都以「本条」开头，或整段是解释性措辞。
# 另外要防 HTML 残留（&nbsp;、<div>、&lt;）。

def _gb55031() -> list:
    return [c for c in load_clauses().values() if c.standard == "GB55031-2022"]


def test_gb55031_is_loaded():
    clauses = _gb55031()
    assert len(clauses) >= 120, f"只录到 {len(clauses)} 条，抓取可能漏页"
    for c in clauses:
        assert c.verbatim.strip(), f"{c.clause_id} 缺原文"


def test_gb55031_ids_are_prefixed():
    for c in _gb55031():
        assert c.clause_id.startswith("GB55031-2022/"), c.clause_id
        bare = c.clause_id.split("/", 1)[1]
        assert re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{1,2}", bare), c.clause_id


def test_gb55031_body_is_not_commentary():
    """「条文说明」段不能混进 verbatim。"""
    for c in _gb55031():
        body = c.verbatim
        assert "条文说明" not in body, c.clause_id
        assert not body.startswith("本条"), f"{c.clause_id} 像是说明文字：{body[:30]}"
        assert "本条是对" not in body, c.clause_id


def test_gb55031_body_has_no_html_residue():
    for c in _gb55031():
        body = c.verbatim
        for bad in ("&nbsp;", "&lt;", "&gt;", "<div", "</div", "\xa0"):
            assert bad not in body, f"{c.clause_id} 混入了 {bad!r}"


def test_gb55031_every_clause_has_chapter():
    for c in _gb55031():
        assert c.chapter.strip(), f"{c.clause_id} 没有归章"


def test_gb55031_severity_follows_wording():
    must = re.compile(r"(?<![相响适供对反])应|必须|不得|严禁|禁止")
    for c in _gb55031():
        if must.search(c.verbatim):
            assert c.severity_hint.value == "error", c.clause_id


# ---------------------------------------------------------------- GB 55019

def _gb55019() -> list:
    return [c for c in load_clauses().values() if c.standard == "GB55019-2021"]


def test_gb55019_is_loaded():
    clauses = _gb55019()
    assert len(clauses) >= 100, f"只录到 {len(clauses)} 条，抓取可能漏页"
    for c in clauses:
        assert c.verbatim.strip(), f"{c.clause_id} 缺原文"


def test_gb55019_ids_are_prefixed():
    for c in _gb55019():
        assert c.clause_id.startswith("GB55019-2021/"), c.clause_id
        bare = c.clause_id.split("/", 1)[1]
        assert re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{1,2}", bare), c.clause_id


def test_gb55019_body_is_not_commentary():
    for c in _gb55019():
        body = c.verbatim
        assert "条文说明" not in body, c.clause_id
        assert not body.startswith("本条"), f"{c.clause_id} 像是说明文字：{body[:30]}"


def test_gb55019_has_the_numeric_thresholds():
    """无障碍规范的价值全在数字上，抽样确认没在抓取时丢数值。"""
    text = " ".join(c.verbatim for c in _gb55019())
    for token in ("1.50m", "1.20m", "0.85m", "1.00m", "1.10m"):
        assert token in text, f"没找到 {token}，数值可能在切分时被截断"


def test_gb55019_severity_follows_wording():
    must = re.compile(r"(?<![相响适供对反])应|必须|不得|严禁|禁止")
    for c in _gb55019():
        if must.search(c.verbatim):
            assert c.severity_hint.value == "error", c.clause_id


# ---------------------------------------------------------------- GB 55038

def _gb55038() -> list:
    """GB55038 的正文条文。排除 preface 卡片——那是整个标准的元数据（公告、
    实施日期、废止清单），不是条文，id 也不符合章节号格式。"""
    return [c for c in load_clauses().values()
            if c.standard == "GB55038-2025" and not c.clause_id.endswith("/preface")]


def test_gb55038_is_loaded():
    clauses = _gb55038()
    assert len(clauses) >= 110, f"只录到 {len(clauses)} 条，抓取可能漏页"
    for c in clauses:
        assert c.verbatim.strip(), f"{c.clause_id} 缺原文"


def test_gb55038_ids_are_prefixed():
    for c in _gb55038():
        assert c.clause_id.startswith("GB55038-2025/"), c.clause_id
        bare = c.clause_id.split("/", 1)[1]
        assert re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{1,2}", bare), c.clause_id


def test_gb55038_body_is_not_commentary():
    for c in _gb55038():
        body = c.verbatim
        assert "条文说明" not in body, c.clause_id
        assert not body.startswith("本条"), f"{c.clause_id} 像是说明文字：{body[:30]}"


def test_gb55038_has_no_section_index_leak():
    """章首的节目录行（「2.1 规模与布局2.2 建设要求…」）曾被拼进上一条条文末尾。"""
    tail = re.compile(
        r"(?:\d{1,2}\.\d{1,2}\s*[\u4e00-\u9fa5][\u4e00-\u9fa5、，]{1,11}){2,}"
    )
    for c in _gb55038():
        m = tail.search(c.verbatim)
        assert not m, f"{c.clause_id} 尾部混入节目录：{m.group(0) if m else ''}"


def test_gb55038_has_no_abolition_list_leak():
    """公告废止的是 GB 50096 那批条文，清单不能混进正文。"""
    for c in _gb55038():
        body = c.verbatim
        assert "同时废止" not in body, c.clause_id
        assert not re.search(r"GB\s?\d{5}-\d{4}\s*第", body), f"{c.clause_id} 像是废止清单"


def test_gb55038_covers_the_dwelling_core_sections():
    """住宅规范的价值集中在套内空间与公共空间，这两节不能空。"""
    chapters = {c.chapter for c in _gb55038()}
    for need in ("4.1 套内空间", "4.2 公共空间"):
        assert need in chapters, f"缺 {need}，抓取时可能漏了整节"


def test_gb55038_severity_follows_wording():
    must = re.compile(r"(?<![相响适供对反])应|必须|不得|严禁|禁止")
    for c in _gb55038():
        if must.search(c.verbatim):
            assert c.severity_hint.value == "error", c.clause_id


# ---------------------------------------------------------------- 附表

def _tables() -> list:
    from cadspec.kb import load_tables
    return load_tables()


def test_tables_are_loaded_and_shaped():
    tables = _tables()
    assert len(tables) >= 7, f"只加载到 {len(tables)} 张表"
    for t in tables:
        assert t.get("table_id"), t
        assert t.get("rows"), f'{t.get("table_id")} 没有数据行'
        n = len(t["header"])
        for row in t["rows"]:
            assert len(row) == n, f'{t["table_id"]}: 「{row[0]}」列数 {len(row)}，表头 {n}'


def test_tables_point_at_real_clauses():
    clauses = load_clauses()
    for t in _tables():
        ref = t.get("clause")
        assert ref in clauses, f'{t.get("table_id")} 挂在 {ref}，知识库里没有这条'


def test_stair_table_numbers_are_right():
    """表 5.3.9 转录后最容易错的一处：住宅楼梯最大高度 0.175 不是 0.165。"""
    t = next(x for x in _tables() if x["table_id"] == "表5.3.9")
    heights = {row[0]: row[2] for row in t["rows"]}
    assert heights["住宅建筑公共楼梯、以电梯作为主要垂直交通的多层公共建筑和高层建筑裙房的楼梯"] == "0.175"
    assert heights["以楼梯作为主要垂直交通的公共建筑、非住宅类居住建筑的楼梯"] == "0.165"
    assert heights["以电梯作为主要垂直交通的高层和超高层建筑楼梯"] == "0.180"


def test_evacuation_width_table_numbers_are_right():
    t = next(x for x in _tables() if x["table_id"] == "表7.4.7")
    rows = {row[0]: row[1:] for row in t["rows"]}
    assert rows["地上 1 层～2 层"] == ["0.65", "0.75", "1.00"]
    assert rows["地上 不小于 4 层"] == ["1.00", "1.25", "—"]
    assert rows["地下、半地下 埋深大于 10m"][0] == "1.00"


def test_linewidth_table_columns_are_consistent():
    t = next(x for x in _tables() if x["table_id"] == "表4.0.1")
    headers = t["header"]
    assert headers[0] == "线宽比"
    groups = {h: [row[i] for row in t["rows"]]
              for i, h in enumerate(headers) if i > 0}
    for name, vals in groups.items():
        assert len(vals) == 4, name
        # 每组内部必须递减：b > 0.7b > 0.5b > 0.25b
        nums = [float(v) for v in vals]
        assert nums == sorted(nums, reverse=True), f"{name} 组不递减：{vals}"


def test_kb_self_check_covers_tables():
    from cadspec.kb import validate_kb
    problems = validate_kb(load_clauses(), load_rules(), _tables())
    assert not problems, problems

def test_no_section_index_leak_in_any_kb():
    """任何 KB 的 verbatim 尾部都不该残留「3.2 场地3.3 配套设施」这种节索引。

    来源网页每章开头有一行节目录，有时独立成行、有时粘在上一条句号后面。
    早期只在按行切分阶段过滤，join 之后又拼回去了。
    """
    pat = re.compile(
        r"(?:\d{1,2}\.\d{1,2}\s*[\u4e00-\u9fa5][\u4e00-\u9fa5、，（）]{1,14}){2,}\s*$"
    )
    from pathlib import Path
    import yaml

    for path in sorted(Path("data/kb").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for c in data["clauses"]:
            tail = c["verbatim"].strip()
            assert not pat.search(tail), f"{c['clause_id']} 尾部残留节索引：…{tail[-40:]}"

# ---------------------------------------------------------------- 图层空置 / 比例一致性 / 尺寸数量

def test_empty_layer_is_flagged(tmp_path):
    """定义了却不装实体的图层 -> §13.0.1 第 1 款（宜）-> warning。"""
    doc = _doc()
    doc.layers.add("剖切符号", color=7)  # 建了层，不往里放东西
    found = _findings(tmp_path, doc)
    assert found["R-LAYER-003"], "空图层应当被指出"
    assert "剖切符号" in (found["R-LAYER-003"][0].sample or [])
    assert found["R-LAYER-003"][0].cite == "GBT50001-2017/13.0.1"


def test_used_layer_is_not_flagged(tmp_path):
    """往层里放一个实体之后，它就不该再出现在空图层清单里。"""
    doc = _doc()
    doc.layers.add("楼梯", color=4)
    doc.modelspace().add_line((0, 0), (100, 0), dxfattribs={"layer": "楼梯"})
    found = _findings(tmp_path, doc)
    sample = found["R-LAYER-003"][0].sample if found["R-LAYER-003"] else []
    assert "楼梯" not in sample


def test_scale_mismatch_is_an_error(tmp_path):
    """图上写 1:50、按 1:100 算 -> 所有打印尺寸换算错位 -> error。"""
    doc = _doc()
    doc.modelspace().add_text("1:50", height=500, dxfattribs={"layer": "标注"})
    found = _findings(tmp_path, doc, scale=100)
    assert found["R-SCALE-001"], "比例不一致应当命中"
    assert found["R-SCALE-001"][0].severity.value == "error"
    assert found["R-SCALE-001"][0].cite == "SJD-2016/4.3.4-20"


def test_scale_match_passes(tmp_path):
    doc = _doc()
    doc.modelspace().add_text("1:100", height=500, dxfattribs={"layer": "标注"})
    assert _findings(tmp_path, doc, scale=100)["R-SCALE-001"] == []


def test_scale_absent_does_not_fire_consistency_rule(tmp_path):
    """图里没有比例时，这条不该报——缺比例是 R-DIM-002 的事，两条不重叠。"""
    doc = _doc()
    found = _findings(tmp_path, doc, scale=100)
    assert found["R-SCALE-001"] == []
    assert found["R-DIM-002"], "缺比例仍应由 R-DIM-002 报"


def test_too_few_dimensions_is_a_warning(tmp_path):
    doc = _doc()
    msp = doc.modelspace()
    msp.add_linear_dim(base=(0, 500), p1=(0, 0), p2=(1000, 0),
                       dxfattribs={"layer": "标注"}).render()
    found = _findings(tmp_path, doc)
    assert found["R-DIM-003"], "只有 1 个标注，少于要求的 3 个"
    assert found["R-DIM-003"][0].severity.value == "warning"


def test_enough_dimensions_pass(tmp_path):
    doc = _doc()
    msp = doc.modelspace()
    for i in range(3):
        msp.add_linear_dim(base=(0, 500 + i * 200), p1=(0, 0), p2=(1000, 0),
                           dxfattribs={"layer": "标注"}).render()
    assert _findings(tmp_path, doc)["R-DIM-003"] == []


# ---------------------------------------------------------------- 规则身份与契约

def test_finding_ids_always_come_from_the_rule(tmp_path):
    """Finding 的 rule_id 必须是调用它的那条规则的 id。

    同一个 check 会被多条规则复用（不同条文、不同阈值）。函数里写死的 id
    一旦被复用就会张冠李戴，所以 run_rules 必须无条件覆盖。
    """
    doc = _doc()
    path = tmp_path / "case.dxf"
    doc.saveas(path)
    ctx = build_context(path, scale=100)
    rules = load_rules()
    known = {r.rule_id for r in rules}
    for rule, fs in run_rules(ctx, rules):
        for f in fs:
            assert f.rule_id == rule.rule_id, f"{f.rule_id} != {rule.rule_id}"
            assert f.rule_id in known


def test_cited_clause_must_declare_checkable():
    """被规则引用的条文必须在 machine 里声明 checkable。

    这条契约防的是：规则声称依据某条规范，而那条规范从未表示过自己能判。
    """
    from cadspec.model import Clause, Rule, Severity

    clause = Clause(
        clause_id="X/1", standard="X", standard_name="测试标准", chapter="1 测试",
        verbatim="测试条文", machine=None,
    )
    rule = Rule(rule_id="R-X", title="t", severity=Severity.INFO,
                check="unit_not_mm", params={}, cite="X/1")
    problems = validate_kb({"X/1": clause}, [rule], [])
    assert any("checkable" in p for p in problems), problems


def test_checkable_none_cannot_be_cited():
    from cadspec.model import Clause, Rule, Severity

    clause = Clause(
        clause_id="X/1", standard="X", standard_name="测试标准", chapter="1 测试",
        verbatim="测试条文", machine={"checkable": "none"},
    )
    rule = Rule(rule_id="R-X", title="t", severity=Severity.INFO,
                check="unit_not_mm", params={}, cite="X/1")
    problems = validate_kb({"X/1": clause}, [rule], [])
    assert any("none" in p for p in problems), problems


def test_bad_checkable_value_is_rejected():
    from cadspec.model import Clause, Rule, Severity

    clause = Clause(
        clause_id="X/1", standard="X", standard_name="测试标准", chapter="1 测试",
        verbatim="测试条文", machine={"checkable": "maybe"},
    )
    rule = Rule(rule_id="R-X", title="t", severity=Severity.INFO,
                check="unit_not_mm", params={}, cite="X/1")
    problems = validate_kb({"X/1": clause}, [rule], [])
    assert any("不是合法值" in p for p in problems), problems


def test_every_cited_clause_in_real_kb_is_declared():
    """真实知识库里，被引用的条文全部有 checkable。"""
    problems = validate_kb(load_clauses(), load_rules(), _tables())
    assert not problems, problems

# ---------------------------------------------------------------- 房间尺度（GB 55038 4.1）
#
# 这一组是第一批"看得懂图纸实体"的规则：墙线围出房间，图上文字给出用途，
# 两者一碰就能判房间够不够大、够不够宽。

def _mline_style(doc, name, offsets):
    from ezdxf.entities.mline import MLineStyleElement, MLineStyleElements

    els = MLineStyleElements()
    for off in offsets:
        els.elements.append(MLineStyleElement(off, 2, "BYLAYER"))
    st = doc.mline_styles.new(name)
    st.elements = els
    return st


def _room_doc(tmp_path, w, h, label, thickness=200):
    doc = _doc()
    _mline_style(doc, "WALL", (0.5, -0.5))
    msp = doc.modelspace()
    pts = [(0, 0), (w, 0), (w, h), (0, h), (0, 0)]
    for a, b in zip(pts, pts[1:]):
        msp.add_mline([a, b], dxfattribs={
            "layer": "外墙", "style_name": "WALL", "scale_factor": thickness,
        })
    if label:
        msp.add_text(label, height=200, dxfattribs={"layer": "标注"}).set_placement((w / 2, h / 2))
    path = tmp_path / "room.dxf"
    doc.saveas(path)
    return path


def _room_findings(tmp_path, w, h, label):
    path = _room_doc(tmp_path, w, h, label)
    ctx = build_context(path, scale=100, level="strict")
    return {r.rule_id: fs for r, fs in run_rules(ctx, load_rules())}, ctx


def test_room_purpose_comes_from_text(tmp_path):
    """图上的「卧室」两个字要能落到房间里。"""
    _, ctx = _room_findings(tmp_path, 4000, 3000, "卧室")
    assert len(ctx.rooms) == 1
    assert ctx.rooms[0]["label"] == "卧室"
    assert ctx.rooms[0]["purpose"] == "卧室"


def test_net_area_is_smaller_than_axis_area(tmp_path):
    """净面积必须小于墙中心线围出的面积，差值约为周长×半个墙厚。"""
    _, ctx = _room_findings(tmp_path, 4000, 3000, "卧室")
    r = ctx.rooms[0]
    assert abs(r["area_m2"] - 12.0) < 0.01
    assert r["net_area_m2"] < r["area_m2"]
    # 净尺寸 3800×2800 = 10.64m²
    assert abs(r["net_area_m2"] - 10.64) < 0.05


def test_small_bedroom_hits_both_rules(tmp_path):
    """3.0×1.5m 的卧室：面积和短边都不够，两条规则各报一条。"""
    found, ctx = _room_findings(tmp_path, 3000, 1500, "卧室")
    assert ctx.rooms[0]["net_area_m2"] < 5.0
    assert found["R-ROOM-001"], "面积不足应命中"
    assert found["R-ROOM-002"], "短边不足应命中"
    assert found["R-ROOM-001"][0].severity.value == "error"


def test_big_bedroom_passes(tmp_path):
    found, _ = _room_findings(tmp_path, 4000, 3600, "卧室")
    assert found["R-ROOM-001"] == []
    assert found["R-ROOM-002"] == []


def test_kitchen_below_3_5_sqm(tmp_path):
    """2.8×1.2m 轴线 → 净 2.6×1.0 = 2.6m²，低于 3.5。"""
    found, ctx = _room_findings(tmp_path, 2800, 1200, "厨房")
    assert ctx.rooms[0]["purpose"] == "厨房"
    assert found["R-ROOM-004"], "厨房面积不足应命中"


def test_unlabeled_room_is_not_judged(tmp_path):
    """图上没写名字的房间不参与判定，不能凭面积猜。"""
    found, ctx = _room_findings(tmp_path, 3000, 1500, None)
    assert ctx.rooms[0]["purpose"] is None
    assert found["R-ROOM-001"] == []
    assert found["R-ROOM-002"] == []


def test_basic_level_skips_geometry(tmp_path):
    """basic 档不做几何识别，房间规则自然全部不命中。"""
    path = _room_doc(tmp_path, 3000, 1500, "卧室")
    ctx = build_context(path, scale=100, level="basic")
    assert ctx.rooms == []
    assert ctx.geometry_level == "basic"
    found = {r.rule_id: fs for r, fs in run_rules(ctx, load_rules())}
    assert found["R-ROOM-001"] == []

# ---------------------------------------------------------------------------
# 门宽分档 + 图面完整性
#
# 门宽分档要用手工构造的 ReviewContext 测：造一张带门扇弧、又恰好卡在
# 0.80m 和 0.90m 之间的 DXF 太费事，而这条规则的逻辑只依赖 rooms/openings
# 两个字段，直接喂数据更清楚。
# ---------------------------------------------------------------------------

def _min_ctx(rooms=None, openings=None, **kw):
    from cadspec.model import ReviewContext

    base = dict(
        path="x.dxf", dxfversion="AC1032", insunits=4, ltscale=1.0,
        limmin=(0, 0), limmax=(420, 297), scale=100,
        layers=[], texts=[], circles=[], mlines=[],
        entity_counts={}, layer_entity_counts={}, dim_count=0,
        block_names=[], layout_names=["Model"], extents=None,
        rooms=rooms or [], openings=openings or [],
        geometry_level="strict",
    )
    base.update(kw)
    return ReviewContext(**base)


def _box(purpose, area, short, nbrs=None, label=None):
    return {
        "id": "R1", "label": label or purpose, "purpose": purpose,
        "center": (2000, 1500), "area_m2": area + 0.8, "net_area_m2": area,
        "short_side": short + 200, "net_short_side": short,
        "outline": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
        "neighbors": nbrs or [],
    }


def _door(oid, width, start, end):
    return {"oid": oid, "kind": "door", "filled": True,
            "width": width, "start": start, "end": end}


def test_bedroom_door_below_840_is_an_error():
    """卧室门 0.80m 净宽 → 洞口要 840。750 的洞口不够。"""
    room = _box("卧室", 12.0, 3000,
                [{"room": "R2", "opening": "O1", "width": 750, "kind": "door"}])
    op = _door("O1", 750, (4000, 1000), (4000, 1750))
    found = {r.rule_id: fs for r, fs in run_rules(_min_ctx([room], [op]), load_rules())}
    assert found["R-OPEN-004"], "卧室门 750 应命中"
    assert found["R-OPEN-004"][0].severity.value == "error"


def test_bedroom_door_at_840_passes():
    room = _box("卧室", 12.0, 3000,
                [{"room": "R2", "opening": "O1", "width": 840, "kind": "door"}])
    op = _door("O1", 840, (4000, 1000), (4000, 1840))
    found = {r.rule_id: fs for r, fs in run_rules(_min_ctx([room], [op]), load_rules())}
    assert found["R-OPEN-004"] == []


def test_kitchen_door_is_not_judged_by_bedroom_threshold():
    """厨卫门 0.70m 就够，同一条 750 的洞口不该被卧室门标准判。"""
    room = _box("厨房", 6.0, 2000,
                [{"room": "R2", "opening": "O1", "width": 750, "kind": "door"}])
    op = _door("O1", 750, (4000, 1000), (4000, 1750))
    found = {r.rule_id: fs for r, fs in run_rules(_min_ctx([room], [op]), load_rules())}
    assert found["R-OPEN-004"] == []


def test_entrance_door_below_940_is_an_error():
    """外轮廓上的门按户门判：0.90m 净宽 → 洞口 940。"""
    room = _box("起居室", 20.0, 3000)
    op = _door("O1", 900, (1000, 0), (1900, 0))   # 中点 (1450,0) 在轮廓上
    found = {r.rule_id: fs for r, fs in run_rules(_min_ctx([room], [op]), load_rules())}
    assert found["R-OPEN-005"], "户门 900 应命中"
    assert found["R-OPEN-005"][0].severity.value == "error"


def test_interior_door_is_not_an_entrance():
    """离轮廓 1m 以上的门不算户门，不该触发户门规则。"""
    room = _box("起居室", 20.0, 3000)
    op = _door("O1", 900, (1000, 1500), (1900, 1500))
    found = {r.rule_id: fs for r, fs in run_rules(_min_ctx([room], [op]), load_rules())}
    assert found["R-OPEN-005"] == []


def test_toilet_below_2_5_sqm(tmp_path):
    """2.0×1.2m 轴线 → 净 1.8×1.0 = 1.8m²，低于卫生间下限 2.5。"""
    found, ctx = _room_findings(tmp_path, 2000, 1200, "卫生间")
    assert ctx.rooms[0]["purpose"] == "卫生间"
    assert found["R-ROOM-005"], "卫生间面积不足应命中"


def test_completeness_lists_what_is_missing(tmp_path):
    """完整性清单要把「没画的东西」指出来，而不是只说「通过」。"""
    path = _room_doc(tmp_path, 4000, 3600, "卧室")
    ctx = build_context(path, scale=100, level="strict")
    comp = ctx.completeness
    assert comp["items"], "应有完整性清单"
    keys = {i["key"] for i in comp["items"]}
    assert {"room_label", "stairs", "dimensions", "scale"} <= keys
    by_key = {i["key"]: i for i in comp["items"]}
    assert by_key["room_label"]["ok"] is True      # 1/1 有名字
    assert by_key["dimensions"]["ok"] is False     # 没有任何尺寸标注
    assert by_key["scale"]["ok"] is False          # 没有比例文字


def test_completeness_flags_unlabeled_rooms(tmp_path):
    path = _room_doc(tmp_path, 4000, 3600, None)
    ctx = build_context(path, scale=100, level="strict")
    by_key = {i["key"]: i for i in ctx.completeness["items"]}
    assert by_key["room_label"]["ok"] is False


def test_completeness_in_basic_level_is_unknown_not_pass(tmp_path):
    """basic 档不做几何识别，房间项要标「不适用」而不是「通过」。"""
    path = _room_doc(tmp_path, 4000, 3600, "卧室")
    ctx = build_context(path, scale=100, level="basic")
    by_key = {i["key"]: i for i in ctx.completeness["items"]}
    assert by_key["room_label"]["ok"] is None
    assert ctx.completeness["ok"] < ctx.completeness["applicable"]
