# -*- coding: utf-8 -*-
"""给不碰命令行的人用的最小窗口：选图 → 体检 / 检查 → 看报告。

命令行能做的事它都能做，只是把参数变成了按钮。检查跑在后台线程里，
大图（几万实体）跑十几秒时窗口不会假死。
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import font as tkfont
from tkinter import scrolledtext, ttk

APP_TITLE = "cadspec — 建筑图纸合规检查"
LEVELS = ("宽松（只看图纸表达）", "严格（含房间与门窗尺度）")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.path = tk.StringVar()
        self.scale = tk.StringVar(value="100")
        self.level = tk.StringVar(value=LEVELS[1])
        self.status = tk.StringVar(value="先选一张 DXF 图纸")
        self._q: queue.Queue[tuple[str, str]] = queue.Queue()
        self._busy = False

        root.title(APP_TITLE)
        root.geometry("980x720")
        root.minsize(760, 520)

        base = tkfont.nametofont("TkDefaultFont").actual()
        ui_family = base.get("family", "Microsoft YaHei UI")
        self.f_ui = tkfont.Font(family=ui_family, size=10)
        self.f_mono = tkfont.Font(family="Consolas", size=10)

        self._build()
        self.root.after(120, self._drain)

    # ---------- 界面 ----------
    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}

        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="图纸", font=self.f_ui).pack(side="left")
        entry = ttk.Entry(top, textvariable=self.path, font=self.f_ui)
        entry.pack(side="left", fill="x", expand=True, padx=(8, 6))
        ttk.Button(top, text="选择…", command=self.pick).pack(side="left")
        ttk.Button(top, text="体检", command=lambda: self.run("doctor")).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="检查", command=lambda: self.run("check")).pack(side="left", padx=(6, 0))

        opts = ttk.Frame(self.root)
        opts.pack(fill="x", **pad)

        ttk.Label(opts, text="出图比例 1:", font=self.f_ui).pack(side="left")
        ttk.Entry(opts, textvariable=self.scale, width=7, font=self.f_ui).pack(side="left", padx=(4, 16))

        ttk.Label(opts, text="档位", font=self.f_ui).pack(side="left")
        ttk.Combobox(
            opts, textvariable=self.level, values=LEVELS, state="readonly", width=24, font=self.f_ui
        ).pack(side="left", padx=(6, 16))

        ttk.Button(opts, text="保存报告…", command=self.save).pack(side="left")
        ttk.Button(opts, text="清空", command=lambda: self.out.delete("1.0", "end")).pack(side="left", padx=(6, 0))

        self.out = scrolledtext.ScrolledText(
            self.root, wrap="word", font=self.f_mono, undo=False,
            bg="#12161c", fg="#d7dde5", insertbackground="#d7dde5",
            relief="flat", padx=12, pady=10,
        )
        self.out.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        self._splash()

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(bar, textvariable=self.status, font=self.f_ui, foreground="#5b6472").pack(side="left")

    def _splash(self) -> None:
        self.out.insert(
            "end",
            "这是中国建筑规范的检查工具。\n\n"
            "用法：\n"
            "  1. 点「选择…」挑一张 .dxf 图纸\n"
            "  2. 先点「体检」——它告诉你这张图能不能被读懂，要改哪几处\n"
            "  3. 再点「检查」——它逐条判合规，每条写明依据第几条、错在哪个实体\n\n"
            "「严格」档会尝试识别房间和门窗，要求墙用多线（MLINE）画在\n"
            "「外墙」「内墙」图层。不满足时它会降级并在报告里说明，不会静默出错。\n\n"
            "能自动判定的条文约占全部条文的 3%，其余是供检索和解释用的。\n"
            "报告里写「判不了」的，不会假装通过。\n",
        )
        self.out.configure(state="disabled")

    # ---------- 动作 ----------
    def pick(self) -> None:
        p = filedialog.askopenfilename(
            title="选择 DXF 图纸",
            filetypes=[("DXF 图纸", "*.dxf"), ("所有文件", "*.*")],
        )
        if p:
            self.path.set(p)
            self.status.set(Path(p).name)

    def _append(self, text: str) -> None:
        self.out.configure(state="normal")
        self.out.insert("end", text)
        self.out.configure(state="disabled")

    def _scroll_top(self) -> None:
        """报告要从头读，所以跑完停在顶部，而不是末尾。"""
        self.out.yview_moveto(0.0)

    def _clear(self) -> None:
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.configure(state="disabled")

    def run(self, mode: str) -> None:
        if self._busy:
            return
        raw = self.path.get().strip().strip('"')
        if not raw:
            messagebox.showinfo(APP_TITLE, "先选一张 DXF 图纸。")
            return
        path = Path(raw)
        if not path.exists():
            messagebox.showerror(APP_TITLE, f"找不到这个文件：\n{path}")
            return
        try:
            scale = int(self.scale.get().strip())
            if scale <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_TITLE, "出图比例要填正整数，比如 1:100 就填 100。")
            return

        level = "strict" if self.level.get() == LEVELS[1] else "basic"
        self._clear()
        self._append(f"正在处理 {path.name} …\n")
        self._busy = True
        self.status.set("正在跑，大图可能要十几秒…")
        threading.Thread(target=self._work, args=(mode, path, scale, level), daemon=True).start()

    def _work(self, mode: str, path: Path, scale: int, level: str) -> None:
        # 用绝对导入：这个文件会被 PyInstaller 当作脚本入口跑，
        # 那时 __name__ 是 "__main__"，相对导入（from . import）会报
        # "attempted relative import with no known parent package"。
        try:
            from cadspec import __version__
            if mode == "doctor":
                from cadspec import doctor
                rep = doctor.diagnose(path)
                text = doctor.render_text(rep, version=__version__)
            else:
                from cadspec import report
                from cadspec.kb import load_clauses, load_rules
                from cadspec.review import build_context
                from cadspec.rules import run_rules
                ctx = build_context(path, scale=scale, level=level)
                results = run_rules(ctx, load_rules())
                text = report.render_text(ctx, results, load_clauses(), version=__version__)
            self._q.put(("ok", text))
        except Exception as exc:  # noqa: BLE001 - 窗口里要把任何异常都变成一句话
            self._q.put(("err", f"{type(exc).__name__}: {exc}"))

    def _drain(self) -> None:
        try:
            while True:
                kind, text = self._q.get_nowait()
                self._busy = False
                if kind == "ok":
                    self._clear()
                    self._append(text)
                    self._scroll_top()
                    self.status.set("完成")
                else:
                    self._clear()
                    self._append("这次没跑成。原因：\n\n" + text + "\n\n"
                                 "常见原因：文件不是 DXF、文件被 CAD 占用、或者图太大。\n")
                    self.status.set("失败")
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def save(self) -> None:
        content = self.out.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo(APP_TITLE, "报告还是空的，先跑一次检查。")
            return
        p = filedialog.asksaveasfilename(
            title="保存报告", defaultextension=".md",
            initialfile="审查报告.md",
            filetypes=[("Markdown", "*.md"), ("文本", "*.txt")],
        )
        if p:
            Path(p).write_text(content + "\n", encoding="utf-8")
            self.status.set(f"已保存到 {Path(p).name}")


def main() -> int:
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.35)
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
