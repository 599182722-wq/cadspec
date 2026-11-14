# -*- coding: utf-8 -*-
"""一次性探查：把一张 DXF 的事实摸清楚，再决定规则怎么写。"""
import re, sys, collections, traceback
import ezdxf

sys.stdout.reconfigure(encoding="utf-8")

PATH = sys.argv[1] if len(sys.argv) > 1 else r"D:\dsh\cad\1.dxf"


def dec(s):
    if not isinstance(s, str):
        return s
    return re.sub(r"\\U\+([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), s)


def safe(label, fn):
    try:
        fn()
    except Exception as ex:
        print(f"  !! {label} failed: {type(ex).__name__}: {ex}")


doc = ezdxf.readfile(PATH)
msp = doc.modelspace()
h = doc.header

print("=" * 74)
print(f"FILE   {PATH}")
print(f"DXFVER {doc.dxfversion}  ACADVER={h.get('$ACADVER')}")
print(f"INSUNITS={h.get('$INSUNITS')} (4=mm)  MEASUREMENT={h.get('$MEASUREMENT')}  LTSCALE={h.get('$LTSCALE')}")
print(f"LIMITS {h.get('$LIMMIN')} .. {h.get('$LIMMAX')}")

cnt = collections.Counter(e.dxftype() for e in msp)
print("\n-- ENTITIES --")
for k, v in cnt.most_common():
    print(f"  {k:<14} {v}")

print(f"\n-- LAYERS ({len(doc.layers)}) --")
print(f"  {'name':<14}{'color':>6}  {'linetype':<18}{'lw':>5}  {'plottable':<10}off frozen")
for lyr in doc.layers:
    lw = getattr(lyr.dxf, "lineweight", "n/a")
    print(f"  {lyr.dxf.name:<14}{lyr.dxf.color:>6}  {lyr.dxf.linetype:<18}{str(lw):>5}  "
          f"{str(lyr.dxf.get('plot', 1)):<10}{int(lyr.is_off())}   {int(lyr.is_frozen())}")

print("\n-- MTEXT --")
for e in msp.query("MTEXT"):
    ins = e.dxf.insert
    print(f"  layer={e.dxf.layer:<8} ch={e.dxf.char_height:>7.1f} at=({ins.x:>9.1f},{ins.y:>9.1f}) {dec(e.text)!r}")

print("\n-- TEXT on 文字/标高/标注 --")
for e in msp.query("TEXT"):
    t = dec(e.dxf.text)
    if e.dxf.layer in ("文字", "标高", "标注") and not t.strip().isalnum():
        ins = e.dxf.insert
        print(f"  layer={e.dxf.layer:<8} h={e.dxf.height:>7.1f} at=({ins.x:>9.1f},{ins.y:>9.1f}) {t!r}")

print("\n-- MLINE (墙厚 = scale_factor) --")
sty = collections.Counter()
for e in msp.query("MLINE"):
    sty[(e.dxf.get("style_name", "?"), round(e.dxf.get("scale_factor", 0), 1), e.dxf.layer)] += 1
for (s, sc, lay), n in sty.items():
    print(f"  style={s:<16} scale_factor={sc:>8.1f}  layer={lay:<8} n={n}")
m0 = next(iter(msp.query("MLINE")), None)
if m0 is not None:
    try:
        locs = list(m0.get_locations())
        print(f"  样例顶点数 {len(locs)}, 前4: {[(round(p.x,1), round(p.y,1)) for p in locs[:4]]}")
    except Exception as ex:
        print(f"  get_locations 失败: {type(ex).__name__}: {ex}")

print("\n-- 结构体 --")
print(f"  INSERT   : {len(list(msp.query('INSERT')))}  -> blocks {[b.name for b in doc.blocks if not b.name.startswith('*')]}")
print(f"  DIMENSION: {len(list(msp.query('DIMENSION')))}")
print(f"  LEADER   : {len(list(msp.query('LEADER')))}")
print(f"  layouts  : {[l.name for l in doc.layouts]}")
for lay in doc.layouts:
    if lay.name != "Model":
        print(f"    layout {lay.name!r}: {len(list(lay))} entities")
print(f"  dimstyles: {[d.dxf.name for d in doc.dimstyles]}")
print(f"  styles   : {[s.dxf.name for s in doc.styles]}")

print("\n-- 图元范围 --")
from ezdxf.bbox import extents
try:
    b = extents(msp, fast=True)
    print(f"  extents min={tuple(round(v, 1) for v in b.extmin)} max={tuple(round(v, 1) for v in b.extmax)}")
    print(f"  size   {round(b.size.x, 1)} x {round(b.size.y, 1)} mm   ->  {round(b.size.x/1000,2)} x {round(b.size.y/1000,2)} m")
except Exception as ex:
    print(f"  bbox failed: {type(ex).__name__}: {ex}")

print("\n-- per-layer --")
agg = collections.defaultdict(lambda: {"n": 0, "len": 0.0, "types": collections.Counter()})
for e in msp:
    try:
        lay = e.dxf.layer
    except Exception:
        lay = "?"
    a = agg[lay]
    a["n"] += 1
    a["types"][e.dxftype()] += 1
    if e.dxftype() == "LINE":
        try:
            a["len"] += (e.dxf.end - e.dxf.start).magnitude
        except Exception:
            pass
for lay, a in sorted(agg.items()):
    print(f"  {lay:<10} n={a['n']:<4} linelen={a['len']:>10.0f}mm  {dict(a['types'])}")

print("\n-- HATCH by layer --")
hc = collections.Counter(e.dxf.layer for e in msp.query("HATCH"))
print(" ", dict(hc))
