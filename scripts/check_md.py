# -*- coding: utf-8 -*-
"""检查所有 md 的表格列数、代码块闭合、相对链接是否有效。

GitHub 对 markdown 的容错比本地预览器差：表格列数对不上会整段渲染成纯文本，
相对链接写错就是 404。这些都该在 CI 里拦下来，而不是等推上去才发现。

用法：python scripts/check_md.py，有问题时退出码 1。
"""
import pathlib
import re
import sys

FENCE = "`" * 3
SKIP_PARTS = (".venv", ".cache", "node_modules")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def check_tables(path, lines, problems):
    in_code = False
    block = []
    for i, ln in enumerate(lines, 1):
        if ln.strip().startswith(FENCE):
            in_code = not in_code
            continue
        if in_code:
            continue
        if ln.strip().startswith("|"):
            block.append((i, ln))
        else:
            if block:
                _one_table(path, block, problems)
                block = []
    if block:
        _one_table(path, block, problems)


def _one_table(path, block, problems):
    counts = [(i, len(cells(ln))) for i, ln in block]
    width = counts[0][1]
    for i, n in counts:
        if n != width:
            problems.append(f"{path}:{i}  列数 {n}，表头 {width}")
    if len(block) >= 2:
        second = block[1][1].strip()
        if not re.fullmatch(r"\|[\s:|-]+\|?", second):
            problems.append(f"{path}:{block[1][0]}  第二行不是表格分隔行：{second[:40]}")


def check_fences(path, lines, problems):
    if sum(1 for ln in lines if ln.strip().startswith(FENCE)) % 2:
        problems.append(f"{path}  代码块未闭合（``` 数量为奇数）")


def check_links(path, lines, problems):
    for i, ln in enumerate(lines, 1):
        for target in LINK_RE.findall(ln):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = target.split("#")[0].strip()
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                problems.append(f"{path}:{i}  链接目标不存在：{target}")


def main():
    problems = []
    for p in sorted(pathlib.Path(".").rglob("*.md")):
        if any(part in p.parts for part in SKIP_PARTS):
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        check_tables(p, lines, problems)
        check_fences(p, lines, problems)
        check_links(p, lines, problems)

    if not problems:
        print("OK: 表格、代码块、相对链接全部正常")
    else:
        for item in problems:
            print(item)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
