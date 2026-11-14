"""从 gf.cabr-fire.com 的分章全文页抓条文，输出与 ingest_pdf.py 同构的 YAML。

为什么需要它：住建部官方 PDF 是扫描件（24 页只有 888 个水印字符），
而消防规范网按节发布的是 HTML 文本，正文在 <div id="b_con"> 里，
条文说明单独放在 .twsm 里，正好只要正文。

用法：
    python scripts/ingest_html.py --list https://gf.cabr-fire.com/list-1535.htm \
        --standard GB55031-2022 --name 民用建筑通用规范 -o data/kb/gb55031-2022.yaml
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.request
from pathlib import Path

import yaml

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
LINK_RE = re.compile(r'<a[^>]+href="(article-(\d+)\.htm)"[^>]*>([^<]+)</a>')
CLAUSE_RE = re.compile(r"^(\d{1,2}\.\d{1,2}\.\d{1,2})\s*(.*)$")
ITEM_RE = re.compile(r"^(\d{1,2})\s+(?=\S)")
TAG_RE = re.compile(r"<[^>]+>")
BLOCK_BREAK = re.compile(r"</?(?:div|p|tr|li|br)\b[^>]*>", re.I)

SEV_ERR = re.compile(r"应|必须|不得|严禁|禁止")
SEV_WARN = re.compile(r"宜")
GENERIC = {"一般规定", "基本规定", "总则", "其他工程", "其他规定"}
NUM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(m²|m2|㎡|mm|m|h|min|s|℃|%|层|人|部|个|处|级|lx|kg)"
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")


def extract_body(page: str) -> str | None:
    """取出 <div id="b_con"> 的完整内容。

    不能用非贪婪正则：b_con 内部还有若干 <div>，非贪婪会在第一个
    内层 </div> 处收尾，只拿到第一条。这里做标签配对计数。
    """
    marker = page.find('id="b_con"')
    if marker < 0:
        return None
    open_at = page.rfind("<div", 0, marker)
    if open_at < 0:
        return None
    start = page.find(">", marker) + 1
    depth = 1
    for m in re.finditer(r"<div\b|</div>", page[start:]):
        if m.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                return page[start : start + m.start()]
        else:
            depth += 1
    return None


def clean(fragment: str) -> str:
    s = html.unescape(fragment)
    s = BLOCK_BREAK.sub("\n", s)
    s = TAG_RE.sub("", s)
    s = s.replace("\xa0", " ")
    return s


def split_lines(fragment: str) -> list[str]:
    text = clean(fragment)
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


# 网页里每章开头有一行「本节目录」，形如
#   2.1 规模与布局2.2 建设要求2.3 使用要求
# 有时它是独立一行，有时直接粘在上一条条文的句号后面（「…予以处罚。2.1 规模与布局…」），
# 两种都要去掉，否则会污染 verbatim。
SECNO_RE = re.compile(r"(?<![\d.])\d{1,2}\.\d{1,2}(?![\d.])")
SECTION_TAIL_RE = re.compile(
    # 节标题里可能出现中文括号，如「3.2 公共卫生间（厕所）和无障碍厕所」，
    # 早期的字符类漏了括号，导致 GB55019 的 2.11.4 仍带尾巴。
    r"(?:\d{1,2}\.\d{1,2}\s*[\u4e00-\u9fa5][\u4e00-\u9fa5、，（）]{1,14}){2,}$"
)


def _is_section_index(ln: str) -> bool:
    if len(ln) > 80:
        return False
    if not re.match(r"^\d{1,2}\.\d{1,2}\s", ln):
        return False
    return len(SECNO_RE.findall(ln)) >= 2


def strip_section_tail(ln: str) -> str:
    return SECTION_TAIL_RE.sub("", ln).rstrip()


def infer_severity(body: str) -> str:
    s = body.replace("相应", "").replace("响应", "").replace("适应", "")
    s = s.replace("供应", "").replace("对应", "").replace("反应", "")
    if SEV_ERR.search(s):
        return "error"
    if SEV_WARN.search(s):
        return "warning"
    return "info"


def extract_keywords(section: str, body: str) -> list[str]:
    kws: list[str] = []
    name = re.sub(r"^[\d.]+\s*", "", section).strip()
    if name and name not in GENERIC:
        kws.append(name)
    for m in NUM_RE.finditer(body):
        token = f"{m.group(1)}{m.group(2)}"
        if token not in kws:
            kws.append(token)
    return kws[:12]


def parse_list(list_url: str) -> list[tuple[str, str]]:
    """从目录页取出按出现顺序排列的 (url, title)。"""
    page = fetch(list_url)
    seen: dict[str, str] = {}
    order: list[str] = []
    for m in LINK_RE.finditer(page):
        href, num, title = m.group(1), m.group(2), m.group(3).strip()
        if href not in seen:
            seen[href] = title
            order.append(href)
    base = list_url.rsplit("/", 1)[0]
    return [(f"{base}/{h}", seen[h]) for h in order]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", required=True, help="目录页 URL")
    ap.add_argument("--standard", required=True, help="如 GB55031-2022")
    ap.add_argument("--name", default="")
    ap.add_argument("--source-url", default="")
    ap.add_argument(
        "--source-note",
        default="",
        help="数据来源说明，写进 YAML 顶层，解释为什么不用官方 PDF",
    )
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    pages = parse_list(args.list)
    print(f"目录 {len(pages)} 页")

    clauses: list[dict] = []
    chapter = ""
    cur: dict | None = None
    generic_titles = {"前言", "目 录", "目录"}

    for url, title in pages:
        if any(t in title for t in generic_titles):
            continue
        try:
            page = fetch(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  跳过 {title}：{exc}", file=sys.stderr)
            continue
        h1m = H1_RE.search(page)
        head = re.sub(r"<[^>]+>", "", h1m.group(1)).strip() if h1m else title
        body = extract_body(page)
        if body is None:
            continue
        # b_con 里正文之后紧跟「条文说明」，说明部分同样以条文号开头，
        # 不截断会与正文撞号，并让技术解读混进 verbatim。
        body = re.split(r"条文说明", body, maxsplit=1)[0]
        lines = [strip_section_tail(ln) for ln in split_lines(body)]
        lines = [ln for ln in lines if ln and not _is_section_index(ln)]
        if not lines:
            continue

        if re.match(r"^\d{1,2}\.\d{1,2}\s", head):
            section = head                       # 节标题，如「5.3 楼梯、走廊」
        elif re.match(r"^\d{1,2}\s", head):
            section = head                       # 章标题，如「4 建筑室外场地」
            chapter = head
        else:
            section = head

        for ln in lines:
            mc = CLAUSE_RE.match(ln)
            if mc:
                if cur:
                    clauses.append(cur)
                cur = {
                    "clause_id": mc.group(1),
                    "chapter": section,
                    "raw": [mc.group(2)] if mc.group(2) else [],
                }
            elif cur is not None:
                cur["raw"].append(ln)
    if cur:
        clauses.append(cur)

    out: list[dict] = []
    for c in clauses:
        body = strip_section_tail("".join(c["raw"]))
        out.append(
            {
                "clause_id": f"{args.standard}/{c['clause_id']}",
                "chapter": c["chapter"],
                "verbatim": body,
                "interpret": "",
                "severity_hint": infer_severity(body),
                "keywords": extract_keywords(c["chapter"], body),
                "status": "effective",
                "source_url": args.source_url or args.list,
            }
        )

    doc = {
        "standard": args.standard,
        "standard_name": args.name,
        "source_url": args.source_url or args.list,
        "source_note": args.source_note,
        "clauses": out,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False, width=1000)

    from collections import Counter

    print(f"条文 {len(out)}，章节 {dict(Counter(c['chapter'] for c in out))}")
    print(f"severity {dict(Counter(c['severity_hint'] for c in out))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
