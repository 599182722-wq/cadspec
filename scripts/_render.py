# -*- coding: utf-8 -*-
"""把 DXF 渲染成 PNG，可指定裁剪范围。用完即删。"""
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

path, out = sys.argv[1], sys.argv[2]
x0, x1, y0, y1 = (float(v) for v in sys.argv[3:7])
fig_w = float(sys.argv[7]) if len(sys.argv) > 7 else 24.0

doc = ezdxf.readfile(path)
msp = doc.modelspace()

fig = plt.figure(figsize=(fig_w, fig_w * (y1 - y0) / (x1 - x0)))
ax = fig.add_axes([0, 0, 1, 1])
ax.set_axis_off()
ctx = RenderContext(doc)
Frontend(ctx, MatplotlibBackend(ax)).draw_layout(msp, finalize=False)
ax.set_xlim(x0, x1)
ax.set_ylim(y0, y1)
fig.savefig(out, dpi=170, facecolor="#FFFFFF")
print("saved", out, f"{x1-x0:.0f} x {y1-y0:.0f} mm")
