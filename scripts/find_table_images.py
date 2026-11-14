"""在规范网页里找出「表格图片」的地址。

规范正文里的表格在网页上都是 <img>，条文只写「应符合表 X.Y.Z 的规定」。
这个脚本把每个表格标题和它下面的图片地址对起来，输出清单，
再用 `read_image` 逐张转录进 data/tables/*.yaml。

用法：
    python scripts/find_table_images.py https://gf.cabr-fire.com/list-1535.htm
    python scripts/find_table_images.py <list-url> --only 5.3.9,5.6.4
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from urllib.parse import urljoin

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

ARTICLE_RE = re.compile(r'href="(article-\d+\.htm)"')
# 表格标题：表 5.3.9 / 表5.3.9-1
TABLE_TITLE_RE = re.compile(r"表\s?(\d{1,2}\.\d{1,2}\.\d{1,2}(?:-\d)?)")
IMG_RE = re.compile(r'<img[^>]+?(?:src|href)="([^"]+\.(?:jpg|jpeg|png|gif))"')


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")


def body_of(page: str) -> str:
    """取 b_con 容器，并按 div 配对计入嵌套。"""
    i = page.find('id="b_con"')
    if i < 0:
        return ""
    start = page.find(">", i) + 1
    depth = 1
    pos = start
    for m in re.finditer(r"<div\b|</div>", page[start:]):
        depth += 1 if m.group(0).startswith("<div") else -1
        if depth == 0:
            pos = start + m.start()
            break
    out = page[start:pos]
    cut = out.find("条文说明")
    return out[:cut] if cut > 0 else out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("list_url")
    ap.add_argument("--only", default="", help="只要这些表号，逗号分隔")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    want = {s.strip() for s in args.only.split(",") if s.strip()}
    index = fetch(args.list_url)
    articles = sorted(set(ARTICLE_RE.findall(index)))
    print(f"目录 {args.list_url}：{len(articles)} 个条文页\n")

    found = 0
    for art in articles:
        url = urljoin(args.list_url, art)
        try:
            page = fetch(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {art} 取失败：{exc}")
            continue
        body = body_of(page)
        if not body:
            continue
        # 表格标题后面紧跟的图片，才算这张表的图
        for m in re.finditer(r"表\s?(\d{1,2}\.\d{1,2}\.\d{1,2}(?:-\d)?)[^<]{0,80}", body):
            tid = m.group(1)
            if want and tid not in want:
                continue
            tail = body[m.end():m.end() + 1200]
            img = IMG_RE.search(tail)
            if not img:
                continue
            src = img.group(1)
            if not src.startswith("http"):
                src = urljoin(url, src)
            print(f"表 {tid:<10} {src}")
            print(f"            {url}")
            found += 1
    print(f"\n共 {found} 张")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
