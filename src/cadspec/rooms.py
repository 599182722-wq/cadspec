"""房间用途识别：把图上写的名字对到规范用的类别。

三层降级里的第二层——第一层是块，暂时没做（样例图没有块）；
第三层是人工在 sidecar 文件里指定，也还没做。

这里只做一件事：读模型空间里的文字，看看它落在哪个房间多边形内，
然后把文字归一成用途类别。落在房间外面的文字（轴号、标高）自然被忽略。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import ezdxf
import yaml
from shapely.geometry import Point, Polygon

from .geometry import Geometry, Room

ROOMS_DIR = Path(__file__).resolve().parents[2] / "data" / "rooms"


@dataclass
class TextLabel:
    content: str
    layer: str
    x: float
    y: float


def load_purpose_names(path: Path | None = None) -> dict[str, list[str]]:
    """返回 {用途: [别名...]}，保持 YAML 里的顺序（长的写前面）。"""
    path = path or ROOMS_DIR / "names.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = {}
    for purpose, spec in (data.get("purposes") or {}).items():
        out[purpose] = [a.strip() for a in (spec.get("aliases") or []) if a.strip()]
    return out


def read_texts(path: str | Path) -> list[TextLabel]:
    doc = ezdxf.readfile(str(path))
    out = []
    for e in doc.modelspace():
        if e.dxftype() == "TEXT":
            content = e.dxf.text
            pos = e.dxf.insert
            layer = e.dxf.layer
        elif e.dxftype() == "MTEXT":
            # MTEXT 原文带格式码（{\fSimSun|b0|i0|c134|p2;厨房}），plain_text() 会剥掉
            content = e.plain_text()
            pos = e.dxf.insert
            layer = e.dxf.layer
        else:
            continue
        content = (content or "").strip()
        if content:
            out.append(TextLabel(content, layer, pos.x, pos.y))
    return out


def classify(content: str, names: dict[str, list[str]]) -> str | None:
    """把一段文字归一成用途。多类别命中时取名字最长的那个。"""
    best = None
    best_len = 0
    for purpose, aliases in names.items():
        for alias in aliases:
            if alias and alias in content and len(alias) > best_len:
                best, best_len = purpose, len(alias)
    return best


def label_rooms(geo: Geometry, texts: list[TextLabel],
                names: dict[str, list[str]] | None = None) -> Geometry:
    """给每个房间填 label / purpose，原地修改并返回。

    一个房间里有多个文字时，取面积意义上最靠中心或第一个命中的。
    实测图纸里一个房间通常只有一个名字，复杂的以后再说。
    """
    names = names if names is not None else load_purpose_names()
    for room in geo.rooms:
        if not room.outline or len(room.outline) < 3:
            continue
        poly = Polygon(room.outline).buffer(0)  # buffer(0) 修自交
        for t in texts:
            if not poly.contains(Point(t.x, t.y)):
                continue
            purpose = classify(t.content, names)
            if purpose:
                room.label = t.content
                room.purpose = purpose
                break
    return geo


def annotate(path: str | Path, geo: Geometry | None = None) -> Geometry:
    """读图、抽几何、标用途，一步到位。"""
    if geo is None:
        from .geometry import extract
        geo = extract(path)
    return label_rooms(geo, read_texts(path))
