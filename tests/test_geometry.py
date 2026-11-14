"""几何识别的回归测试。

用真实样例图做回归基线（合成一张带门窗洞口的 MLINE 图太重，性价比不高）。
样例图不在时跳过，不让测试变成对某台机器的依赖。
"""

from pathlib import Path

import pytest

from cadspec.geometry import extract

SAMPLE = Path(r"D:\dsh\cad\1.dxf")

pytestmark = pytest.mark.skipif(
    not SAMPLE.exists(), reason="样例 DXF 不存在"
)


@pytest.fixture(scope="module")
def geo():
    return extract(SAMPLE)


def test_room_count(geo):
    """11 个。曾长期是 10——T 型交点因浮点误差没切开，13.5m² 那间被并进了 R2，
    面积对得上（182.09）所以一直没被发现。量化 noding 之后才露出来。"""
    assert len(geo.rooms) == 11


def test_rooms_tile_the_outline(geo):
    """房间面积之和必须等于外轮廓——不等就说明有漏或重。"""
    assert geo.outer_area_m2 is not None
    assert geo.coverage_ratio == pytest.approx(1.0, abs=0.01)


def test_biggest_and_smallest_room(geo):
    areas = [r.area_m2 for r in geo.rooms]
    assert max(areas) == pytest.approx(46.35, abs=0.1)
    assert min(areas) == pytest.approx(5.40, abs=0.1)


def test_rooms_are_sorted_by_area(geo):
    areas = [r.area_m2 for r in geo.rooms]
    assert areas == sorted(areas, reverse=True)


def test_wall_thickness(geo):
    """墙厚 = scale_factor × 样式里各条线的 offset 跨度，不是 scale_factor 本身。

    1.dxf 的 WALL 样式 offset 是 10 与 -5（跨度 15），比例 20，所以墙厚 300mm；
    WALL-INSIDE 的 offset 是 ±5（跨度 10），比例为 20 的墙厚 200、比例为 10 的 100。
    早先版本直接把 scale_factor 当墙厚，导致净面积内缩量只有实际的十分之一。
    """
    assert geo.wall_thickness["外墙"] == [300.0]
    assert geo.wall_thickness["内墙"] == [100.0, 200.0]


def test_filled_openings_match_window_layer(geo):
    # 门窗层 12 条多线，没有门扇弧的那批按窗计
    windows = [o for o in geo.openings if o.filled and o.kind == "window"]
    assert len(windows) == 12
    widths = sorted(round(o.width) for o in windows)
    assert widths == [900, 900, 1200, 1200, 1500, 1800, 1800, 1800, 1800, 1800, 2400, 2400]


def test_door_arcs_are_separate(geo):
    """门是另外 11 处，靠门扇弧认出来，不在门窗层多线上。"""
    doors = [o for o in geo.openings if o.filled and o.kind == "door"]
    assert len(doors) == 11
    assert all(699 <= o.width <= 1001 for o in doors)


def test_short_side_is_not_larger_than_long_side(geo):
    for r in geo.rooms:
        assert r.short_side <= r.long_side + 1


def test_no_wall_layers_yields_empty():
    """图层名对不上时返回空，而不是抛异常或给出假房间。"""
    geo = extract(SAMPLE, wall_layers=("不存在的图层",), opening_layers=("也不存在",))
    assert geo.rooms == []
    assert geo.outer_area_m2 is None


# --- 房间相邻关系 ---------------------------------------------------------
# 相邻关系是给模型做语义判断用的输入：它需要知道「R7 只通过一扇 800 的门连到 R1」，
# 才能说清楚这是不是厨房。这些测试保证这层数据不会悄悄退化成空的。

def test_every_room_has_neighbors(geo):
    for r in geo.rooms:
        assert r.neighbors, f"R{r.index} 没有任何相邻关系"


def test_neighbors_are_symmetric(geo):
    """A 说与 B 相邻，B 就必须也说与 A 相邻。"""
    byid = {f"R{r.index}": r for r in geo.rooms}
    for r in geo.rooms:
        me = f"R{r.index}"
        for n in r.neighbors:
            other = byid[n["room"]]
            assert any(m["room"] == me for m in other.neighbors), \
                f"{me} 记了 {n['room']}，但对方没记 {me}"


def test_neighbor_openings_exist(geo):
    oids = {o.oid for o in geo.openings}
    assert oids, "洞口没有分配 id"
    for r in geo.rooms:
        for n in r.neighbors:
            if n["kind"] == "wall":
                assert n["opening"] is None, "实墙上不该有洞口"
                continue
            assert n["opening"] in oids, f"相邻关系指向不存在的洞口 {n['opening']}"


def test_small_kitchen_has_single_door(geo):
    """7.92m² 的那间厨房只有一扇 800 的门通向 R1——一条门就是一个灶间的典型形态。

    不按 index 找：房间编号是按面积排序给的，多识别出一间就会整体后移。
    """
    kitchen = next(r for r in geo.rooms if abs(r.area_m2 - 7.92) < 0.05)
    assert len(kitchen.neighbors) == 1
    assert kitchen.neighbors[0]["room"] == "R1"
    assert kitchen.neighbors[0]["width"] == 800
    assert kitchen.neighbors[0]["kind"] == "door"
