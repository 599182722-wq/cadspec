"""从 DXF 还原房间、洞口和墙厚。

这里的做法建立在一个观察上（见 docs/geometry.md）：画图的人把墙画成 MLINE，
墙在门窗处断开，而门窗实体正好填在缺口上。所以只要把线段接起来再做多边形化，
房间就出来了，不需要图像识别。

局限也说清楚：
  - 只认 MLINE 画的墙。用 LINE/PLINE 画墙的图走不通。
  - 只处理正交墙。斜墙会被端点吸附破坏形状。
  - 房间用途认不出来，这里不猜。
"""

from __future__ import annotations

import re

import math
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from shapely import set_precision
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import polygonize, unary_union

DEFAULT_WALL_LAYERS = ("外墙", "内墙")
DEFAULT_OPENING_LAYERS = ("门窗", "门", "窗")

SNAP_TOL = 20.0          # 端点吸附容差 mm
GAP_MIN = 600.0          # 小于此距离视为绘制误差，不当洞口
GAP_MAX = 2000.0         # 视为「同一道墙上的洞口」的最大缺口 mm
MIN_ROOM_AREA = 1_000_000.0  # 1 m²
GRID_MM = 1.0             # 坐标量化网格 mm，用于让 T 型交点真正切开


@dataclass
class Opening:
    """一处洞口。filled=True 表示有门窗实体，False 表示只有缺口。

    kind 是判断出来的门窗类别，unknown 表示图上没有任何可信线索能区分。
    evidence 记录判断依据，报告里原样输出——用户要能看出这个结论是靠什么得出的。
    """

    layer: str
    width: float
    start: tuple[float, float]
    end: tuple[float, float]
    filled: bool
    kind: str = "unknown"          # door / window / unknown
    evidence: str = ""
    style: str = ""                # 多线样式名，用于检测样式是否被复用
    oid: str = ""                  # O1、O2…，供模型在输出里指认

    def as_dict(self) -> dict:
        return {
            "id": self.oid,
            "layer": self.layer,
            "width": round(self.width, 1),
            "start": [round(v, 1) for v in self.start],
            "end": [round(v, 1) for v in self.end],
            "filled": self.filled,
            "kind": self.kind,
            "evidence": self.evidence,
            "style": self.style,
        }


@dataclass
class Room:
    index: int
    area_m2: float
    short_side: float
    long_side: float
    center: tuple[float, float]
    outline: list[tuple[float, float]] = field(default_factory=list)
    label: str | None = None        # 图上写的原文，如「主卧」
    purpose: str | None = None      # 归一后的用途，如「卧室」
    half_wall: float = 100.0        # 围合该房间的墙厚的一半（mm）
    net_area_m2: float = 0.0        # 内缩墙厚后的净使用面积
    net_short_side: float = 0.0     # 内缩后的最小短边
    neighbors: list[dict] = field(default_factory=list)  # 相邻房间与联系方式

    def as_dict(self) -> dict:
        return {
            "id": f"R{self.index}",
            "index": self.index,
            "area_m2": round(self.area_m2, 2),
            "short_side": round(self.short_side),
            "long_side": round(self.long_side),
            "center": [round(v, 1) for v in self.center],
            "label": self.label,
            "purpose": self.purpose,
            "net_area_m2": round(self.net_area_m2, 2),
            "net_short_side": round(self.net_short_side),
            "half_wall": round(self.half_wall, 1),
            "neighbors": self.neighbors,
            "outline": [[round(x, 1), round(y, 1)] for x, y in self.outline],
        }


@dataclass
class Geometry:
    rooms: list[Room]
    openings: list[Opening]
    outer_area_m2: float | None
    coverage_ratio: float | None
    wall_thickness: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "rooms": [r.as_dict() for r in self.rooms],
            "openings": [o.as_dict() for o in self.openings],
            "outer_area_m2": None if self.outer_area_m2 is None else round(self.outer_area_m2, 2),
            "coverage_ratio": None if self.coverage_ratio is None else round(self.coverage_ratio, 3),
            "wall_thickness": self.wall_thickness,
        }


# ---------------------------------------------------------------- 门窗判断
#
# 图上「门」和「窗」不是两种实体，都是墙上的一个洞。要分开只能靠周边线索，
# 按可靠性从高到低：
#   1. 图层名（门窗分层画的图，这是最硬的证据）
#   2. 多线样式名（DOOR / WINDOW）
#   3. 覆盖在洞口上的块名（门块、窗块、编号块）
#   4. 门扇弧线（ARC 的端点落在洞口端点附近，这是门的强特征）
#   5. 洞口旁的编号文字（M 系列是门，C 系列是窗）
#   6. 宽度（最弱，只作冲突提示，不单独定性）
#
# 都给不出结论时返回 unknown，而不是猜一个。猜错比不知道更糟：
# 报错「卧室门只有 700mm」而它其实是窗，用户从此不信这个工具。

DOOR_WORDS = ("门", "DOOR", "MEN")
WINDOW_WORDS = ("窗", "WINDOW", "CHUANG", "CH")
# 常见门窗编号前缀：M=门，C=窗，FM=防火门，TC=凸窗
DOOR_CODE = re.compile(r"^\s*(F?M)\s*[-]?\s*(\d+)\s*$", re.I)
WINDOW_CODE = re.compile(r"^\s*([TC]?C)\s*[-]?\s*(\d+)\s*$", re.I)


def _hit(text: str, words) -> bool:
    up = (text or "").upper()
    return any(w.upper() in up for w in words)


def _nearby(msp, cx: float, cy: float, radius: float = 1200.0):
    """收集洞口附近能用来判断门窗的线索：块名、弧线端点、编号文字。"""
    from shapely.geometry import Point

    pt = Point(cx, cy)
    blocks, arcs, codes = [], [], []
    for e in msp:
        t = e.dxftype()
        if t == "INSERT":
            p = e.dxf.insert
            if pt.distance(Point(p.x, p.y)) <= radius:
                blocks.append(e.dxf.name)
        elif t == "ARC":
            for p in (e.start_point, e.end_point):
                if pt.distance(Point(p.x, p.y)) <= radius:
                    arcs.append(e)
                    break
        elif t in ("TEXT", "MTEXT"):
            content = e.dxf.text if t == "TEXT" else e.plain_text()
            p = e.dxf.insert
            if pt.distance(Point(p.x, p.y)) <= radius and (content or "").strip():
                codes.append(content.strip())
    return blocks, arcs, codes


def _door_arcs(msp) -> list[Opening]:
    """从「门扇开启弧」里读出洞口。

    门在平面图上的标准画法是一条线段加一段弧：铰点在洞口一端，
    线段是门扇停在某处的样子，弧是它扫过的轨迹。所以一条这样的多段线
    直接给出了洞口的两个端点——弧的起点和铰点。

    判据：弧终点（门扇末端）到铰点的距离，必须等于洞口的宽度。
    不等就不是门扇弧——可能是家具、洁具的圆角，忽略掉。
    """
    out: list[Opening] = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE":
            continue
        pts = list(e.get_points("xyb"))
        if len(pts) < 3:
            continue
        arcs = [i for i, p in enumerate(pts) if abs(p[2]) > 1e-6]
        if not arcs:
            continue
        i = arcs[0]
        n = len(pts)
        p0 = pts[i]
        p1 = pts[(i + 1) % n]
        hinge = pts[(i + 2) % n]
        w = math.dist((p0[0], p0[1]), (hinge[0], hinge[1]))
        r = math.dist((p1[0], p1[1]), (hinge[0], hinge[1]))
        if w <= 0 or abs(w - r) > max(30.0, w * 0.05):
            continue
        out.append(Opening(
            layer=e.dxf.layer,
            width=w,
            start=(p0[0], p0[1]),
            end=(hinge[0], hinge[1]),
            filled=True,
            kind="door",
            evidence=(
                f"门扇开启弧：铰点 ({hinge[0]:.0f},{hinge[1]:.0f})，"
                f"弧半径 {r:.0f}mm 与洞口宽 {w:.0f}mm 一致"
            ),
        ))
    return out


def _overlaps(a0, a1, b0, b1, tol: float = 120.0) -> bool:
    """两条洞口线段是否指同一处（端点可比，顺序无关）。"""
    def near(p, q):
        return math.dist(p, q) <= tol
    return (near(a0, b0) and near(a1, b1)) or (near(a0, b1) and near(a1, b0))


def _demote_reused_styles(openings: list[Opening]) -> None:
    """同层同一样式被多个洞口共用时，这个样式名不能当作门窗类型的依据。

    真实场景：画图时复制一条窗的多线去画门，样式名还是 WINDOW。
    这时按样式名判会把门判成窗，而且判错的还是"通过"——假阴性比不判更危险。
    判据：同一 (图层, 样式) 下的洞口宽度跨度超过 1m。门窗两类混用时跨度一定大，
    全是窗或全是门时跨度通常小。
    """
    from collections import defaultdict

    groups: dict[tuple[str, str], list[Opening]] = defaultdict(list)
    for o in openings:
        # 只降级「靠样式名定性」的那些。已经靠几何（门扇弧）定下来的不动。
        if o.filled and o.style and "多线样式" in o.evidence:
            groups[(o.layer, o.style)].append(o)

    for (layer, style), ops in groups.items():
        if len(ops) < 4:
            continue
        widths = [o.width for o in ops]
        span = max(widths) - min(widths)
        if span <= 1000:
            continue
        note = (
            f"「{layer}」层 {len(ops)} 个洞口共用多线样式「{style}」，"
            f"宽度 {min(widths):.0f}~{max(widths):.0f}mm（跨度 {span:.0f}mm）。"
            "同一样式不可能同时既画门又画窗，说明这个样式名被复用，不能当类型依据"
        )
        for o in ops:
            o.kind = "unknown"
            o.evidence = note


def _classify_opening(layer: str, style: str, width: float,
                      blocks, arcs, codes) -> tuple[str, str]:
    """返回 (kind, evidence)。evidence 是给人看的一句话，说明凭什么这么判。"""
    # 1. 图层名。「门窗」这种合并命名两头都含，等于没说，必须排除，
    #    否则整张图的洞口会被一次性判成同一种。
    has_door = _hit(layer, DOOR_WORDS)
    has_window = _hit(layer, WINDOW_WORDS)
    if has_door and not has_window:
        return "door", f"图层名「{layer}」只含门"
    if has_window and not has_door:
        return "window", f"图层名「{layer}」只含窗"

    # 2-3. 多线样式与块名
    if _hit(style, DOOR_WORDS):
        return "door", f"多线样式「{style}」"
    if _hit(style, WINDOW_WORDS):
        if width <= 1000:
            return "unknown", (
                f"多线样式「{style}」指向窗，但宽度 {width:.0f}mm 落在门的常用区间"
                f"（户门 900~1000mm），两者矛盾")
        return "window", f"多线样式「{style}」"
    for b in blocks:
        if _hit(b, DOOR_WORDS):
            return "door", f"洞口上方有块「{b}」"
    for b in blocks:
        if _hit(b, WINDOW_WORDS):
            return "window", f"洞口上方有块「{b}」"

    # 4. 门扇弧
    if arcs:
        return "door", "洞口端部有弧线（门扇开启轨迹）"

    # 5. 编号文字
    for c in codes:
        if DOOR_CODE.match(c):
            return "door", f"洞口旁编号「{c}」"
    for c in codes:
        if WINDOW_CODE.match(c):
            return "window", f"洞口旁编号「{c}」"

    # 6. 宽度：只给提示，不定性
    hint = ""
    if width <= 1000:
        hint = f"；宽度 {width:.0f}mm 更像门"
    return "unknown", "图层名、样式名、块名、弧线、编号文字都没有，无法区分门窗" + hint


def _style_span(doc, name: str) -> float:
    """MLINE 样式的总宽度系数：各条线的 offset 跨度。

    实际墙厚 = scale_factor × offset 跨度。只取 scale_factor 会算错——
    1.dxf 的 WALL 样式 offset 是 10 与 -5，跨度 15，比例 20，
    墙厚是 300mm 而不是 20mm。
    """
    try:
        st = doc.mline_styles.get(name)
        offs = [e.offset for e in st.elements.elements]
    except Exception:
        return 1.0
    if not offs:
        return 1.0
    span = max(offs) - min(offs)
    return span if span > 1e-9 else 1.0


def _read_segments(msp, layers):
    doc = msp.doc
    spans: dict[str, float] = {}
    segs = []
    for e in msp:
        if e.dxftype() != "MLINE" or e.dxf.layer not in layers:
            continue
        pts = [(v.location.x, v.location.y) for v in e.vertices]
        if len(pts) < 2:
            continue
        style = e.dxf.get("style_name") or ""
        if style not in spans:
            spans[style] = _style_span(doc, style)
        segs.append({
            "layer": e.dxf.layer,
            "thickness": abs(e.dxf.get("scale_factor") or 0.0) * spans[style],
            "pts": pts,
            "style": style,
        })
    return segs


def _snap(segs, tol):
    endpoints = [s["pts"][0] for s in segs] + [s["pts"][-1] for s in segs]
    clusters: list[list] = []
    for p in endpoints:
        for c in clusters:
            if math.dist(c[0], p) <= tol:
                c.append(p)
                break
        else:
            clusters.append([p])
    mapping = {}
    for c in clusters:
        cx = sum(q[0] for q in c) / len(c)
        cy = sum(q[1] for q in c) / len(c)
        for q in c:
            mapping[q] = (cx, cy)
    return mapping


def _to_lines(segs, mapping):
    lines = []
    for s in segs:
        pts = [mapping.get(s["pts"][0], s["pts"][0])]
        pts += list(s["pts"][1:-1])
        pts += [mapping.get(s["pts"][-1], s["pts"][-1])]
        lines.append(LineString(pts))
    return lines


def _noded(lines):
    """把一组线切成一个真正的平面图。

    直接 unary_union 不够：一条线的端点落在另一条线中间（T 型交点）时，
    只要坐标差一点点（1e-12 级），shapely 就不会把它切开，polygonize 于是
    认为那个房间没有闭合，把它并进隔壁。表现是房间数偏少、单个面积偏大，
    而且只在某些图上出现——`1.dxf` 的 R2 就被这样吞掉过一个房间。

    先量化到 GRID_MM 再 union，端点就精确相等了。0.01mm 远小于建筑精度，
    不会改变任何房间的面积（1.dxf 量化前后都是 182.09m²）。
    """
    return unary_union(set_precision(MultiLineString(list(lines)), GRID_MM))


def _polys(lines, min_area=MIN_ROOM_AREA):
    return [p for p in polygonize(_noded(lines)) if p.area > min_area]


def _orthogonal(a, b):
    return abs(a[0] - b[0]) < 1.0 or abs(a[1] - b[1]) < 1.0


def _is_vertical(p, q):
    return abs(q[1] - p[1]) > abs(q[0] - p[0])


def _end_orientation(pts, point):
    """端点处的局部走向：True 竖直，False 水平。

    墙段可能是 L 形的，所以不能拿首尾连线判断方向，要看端点相邻的那一段。
    """
    if len(pts) < 2:
        return False
    if math.dist(pts[0], point) <= math.dist(pts[-1], point):
        return _is_vertical(pts[0], pts[1])
    return _is_vertical(pts[-2], pts[-1])


def _aligned_with(seg_pts, point, pa, pb):
    """端点处的墙走向必须与两个端点的连线方向一致。"""
    return _end_orientation(seg_pts, point) == _is_vertical(pa, pb)


def _gap_is_filled(pa, pb, opening_ends, tol):
    """缺口两端是否已经是某个门窗实体的两端——那样就不需要再补。"""
    return any(
        math.dist(pa, oa) <= tol and math.dist(pb, ob) <= tol
        for oa, ob in opening_ends
    )


def _bridge_gaps(wall_segs, opening_ends, tol, gap_min, gap_max):
    bridges = []
    for i, a in enumerate(wall_segs):
        for j in range(i + 1, len(wall_segs)):
            b = wall_segs[j]
            for pa in (a["pts"][0], a["pts"][-1]):
                for pb in (b["pts"][0], b["pts"][-1]):
                    d = math.dist(pa, pb)
                    if not (gap_min <= d <= gap_max) or not _orthogonal(pa, pb):
                        continue
                    if not _aligned_with(a["pts"], pa, pa, pb):
                        continue
                    if not _aligned_with(b["pts"], pb, pb, pa):
                        continue
                    if _gap_is_filled(pa, pb, opening_ends, tol):
                        continue
                    bridges.append((pa, pb, d))
    return bridges


def _short_long_sides(poly: Polygon):
    rect = poly.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)[:4]
    sides = [math.dist(coords[k], coords[(k + 1) % 4]) for k in range(4)]
    return min(sides), max(sides)


def _room_half_wall(poly: Polygon, wall_segs) -> float:
    """房间四周墙厚的一半，取贴在边界上的墙段厚度的中位数。

    墙厚不一致时（外墙 200、内墙 100）取中位数是个折中：
    对矩形房间误差在 50mm 量级，比统一按 100mm 内缩准。
    """
    hs: list[float] = []
    for s in wall_segs:
        t = s.get("thickness") or 0.0
        if t <= 0:
            continue
        try:
            ln = LineString(s["pts"])
        except Exception:
            continue
        if ln.distance(poly) <= 60.0:
            hs.append(t / 2.0)
    if not hs:
        return 100.0
    hs.sort()
    return hs[len(hs) // 2]


def _net_of(poly: Polygon, half: float):
    """内缩半个墙厚得到净空间；房间过窄内缩会消失，此时退回原多边形。"""
    try:
        inner = poly.buffer(-half)
    except Exception:
        return poly.area, *_short_long_sides(poly)
    if inner.is_empty:
        short, long = _short_long_sides(poly)
        return poly.area, short, long
    # buffer 后可能是 MultiPolygon，取最大的一块
    if inner.geom_type == "MultiPolygon":
        inner = max(inner.geoms, key=lambda g: g.area)
    short, long = _short_long_sides(inner)
    return inner.area, short, long


def _link_neighbors(rooms: list[Room], polys: list[Polygon],
                    openings: list[Opening], extra: float = 80.0) -> None:
    """算出房间之间的相邻关系，并指出它们之间是门、窗还是实墙。

    这件事必须由几何算，不能让模型从坐标数组里推——两房间相距 300mm 是隔一道墙，
    相距 0 是共享一道墙，模型看不出这个差别，`Polygon.distance` 一眼就有。
    模型拿到「R3 与 R7 之间是一扇 900mm 的门」之后，只需要回答「这是卧室门吗」。
    """
    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            pa, pb = polys[i], polys[j]
            limit = max(rooms[i].half_wall, rooms[j].half_wall) * 2 + extra
            if pa.distance(pb) > limit:
                continue
            link = None
            for o in openings:
                if o.kind == "gap":
                    continue
                mid = Point((o.start[0] + o.end[0]) / 2, (o.start[1] + o.end[1]) / 2)
                if pa.distance(mid) <= limit and pb.distance(mid) <= limit:
                    link = o
                    break
            entry_i = {"room": f"R{rooms[j].index}"}
            entry_j = {"room": f"R{rooms[i].index}"}
            for e in (entry_i, entry_j):
                e["opening"] = link.oid if link else None
                e["width"] = round(link.width) if link else None
                e["kind"] = link.kind if link else "wall"
            rooms[i].neighbors.append(entry_i)
            rooms[j].neighbors.append(entry_j)


def extract(path: str | Path, wall_layers=DEFAULT_WALL_LAYERS,
            opening_layers=DEFAULT_OPENING_LAYERS, snap_tol=SNAP_TOL,
            gap_min=GAP_MIN, gap_max=GAP_MAX) -> Geometry:
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()

    wall_segs = _read_segments(msp, set(wall_layers))
    opening_segs = _read_segments(msp, set(opening_layers))
    if not wall_segs:
        return Geometry([], [], None, None, {})

    # 门扇弧的端点必须与墙线一起做吸附。它是 LWPOLYLINE，不在 wall_segs /
    # opening_segs 里，若单独处理，端点会和被吸附过的墙端点差零点几毫米，
    # polygonize 就认为没接上，那个房间和隔壁合并——补图时 R5 就是这样丢的。
    arc_doors = _door_arcs(msp)
    has_arcs = bool(arc_doors)
    arc_segs = [{"pts": [o.start, o.end], "layer": o.layer,
                 "thickness": 0.0, "style": ""} for o in arc_doors]

    all_segs = wall_segs + opening_segs + arc_segs
    mapping = _snap(all_segs, snap_tol)
    lines = _to_lines(all_segs, mapping)

    wall_lines = lines[:len(wall_segs)]
    opening_lines = lines[len(wall_segs):len(wall_segs) + len(opening_segs)]
    arc_lines = lines[len(wall_segs) + len(opening_segs):]
    opening_ends = []
    for ln in opening_lines:
        cs = list(ln.coords)
        opening_ends.append((cs[0], cs[-1]))

    # 门扇弧先读——它比任何命名线索都可靠，因为弧是画出来的几何事实。
    openings: list[Opening] = []
    for s, ln in zip(opening_segs, opening_lines):
        p0, p1 = ln.coords[0], ln.coords[-1]
        blocks, arcs, codes = _nearby(msp, (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
        kind, evidence = _classify_opening(
            s["layer"], s.get("style", ""), ln.length, blocks, arcs, codes)
        if has_arcs and kind != "door":
            # 这张图的门都用开启弧表示了，这里没有弧，那就不是门。
            kind, evidence = "window", (
                f"图中 {len(arc_doors)} 处门均以门扇开启弧表示，此洞口无弧"
            )
        openings.append(Opening(
            layer=s["layer"],
            width=ln.length,
            start=p0,
            end=p1,
            filled=True,
            kind=kind,
            evidence=evidence,
            style=s.get("style", ""),
        ))
    openings.extend(arc_doors)

    _demote_reused_styles(openings)

    bridges = _bridge_gaps(wall_segs, opening_ends, snap_tol, gap_min, gap_max)
    for pa, pb, d in bridges:
        if any(_overlaps(pa, pb, o.start, o.end) for o in arc_doors):
            continue
        openings.append(Opening(
            layer="", width=d, start=pa, end=pb, filled=False,
            kind="gap", evidence="墙线之间的缺口，没有门窗实体"))

    # 门扇弧本身也是一个洞口实体，它的开口线必须参与围合。
    # 早先只把 MLINE 门窗放进 opening_lines，弧只能靠 _bridge_gaps 兜底补线；
    # 一旦缺口超过 GAP_MAX 兜不住，那个房间就会和隔壁合并成一个。
    # 弧的端点已经和墙线一起过过同一个 _snap 映射（见上面 all_segs 那一处），
    # 这里直接用切片，不要再拿原始坐标重算一遍。

    # 外轮廓：墙 + 已填充门窗（含门扇弧）
    outer_polys = _polys(wall_lines + opening_lines + arc_lines)
    outer_area = sum(p.area for p in outer_polys) if outer_polys else None

    # 房间：补上未填充缺口后再分割
    bridged = (wall_lines + opening_lines + arc_lines
               + [LineString([a, b]) for a, b, _ in bridges])
    pieces = _polys(bridged)

    rooms = []
    ordered = sorted(pieces, key=lambda p: -p.area)
    for idx, poly in enumerate(ordered, start=1):
        short, long = _short_long_sides(poly)
        c = poly.representative_point()
        half = _room_half_wall(poly, wall_segs)
        net_area, net_short, _ = _net_of(poly, half)
        rooms.append(Room(
            index=idx,
            area_m2=poly.area / 1e6,
            short_side=short,
            long_side=long,
            center=(c.x, c.y),
            outline=[(round(x, 1), round(y, 1)) for x, y in poly.exterior.coords],
            half_wall=half,
            net_area_m2=net_area / 1e6,
            net_short_side=net_short,
        ))

    for n, o in enumerate(openings, start=1):
        o.oid = f"O{n}"
    _link_neighbors(rooms, ordered, openings)

    thickness = {}
    for s in wall_segs:
        if s["thickness"] > 0:
            thickness.setdefault(s["layer"], set()).add(s["thickness"])
    thickness = {k: sorted(v) for k, v in thickness.items()}

    coverage = None
    if outer_area:
        coverage = sum(r.area_m2 * 1e6 for r in rooms) / outer_area

    return Geometry(rooms, openings, None if outer_area is None else outer_area / 1e6,
                    coverage, thickness)
