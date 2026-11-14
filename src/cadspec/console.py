# -*- coding: utf-8 -*-
"""让中文在非 UTF-8 控制台下也能打印。

Windows 的默认代码页是 cp1252（英文系统）或 cp936（中文系统），
`print("体检通过")` 在这两种代码页下都会抛 UnicodeEncodeError——
cp1252 编不出汉字，cp936 编不出部分全角符号。

GitHub 的 windows-latest runner 是英文系统，代码页 1252，
所以在本地跑得好好的脚本，到了 CI 就崩。这不是脚本的逻辑问题，
单纯是输出编码问题，修法也简单：把 stdout/stderr 的编码显式改成 UTF-8。

用 errors="replace" 而不是 "strict"：万一某个字符真的编不出来，
打印一个替代符比让整个程序崩掉好——检查报告的价值在于内容，
不在于每个字符都完美。
"""

from __future__ import annotations

import sys


def fix_console() -> None:
    """把 stdout/stderr 切到 UTF-8。重复调用是安全的。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
