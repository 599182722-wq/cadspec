#!/usr/bin/env python
"""把国标 PDF 拆成条文草稿 YAML。

瓶颈不是"读懂条文"，而是"从 PDF 里拿到干净的条文边界"。国标 PDF 是数字排版、
文本层完整，pymupdf 直接抽就是正文 —— 不需要 OCR，也不需要版面分析模型。

这个脚本负责机械部分：
  1. 逐页抽文本，剔除页眉水印与页码
  2. 识别「x.x 标题」节标题，给其后条文打上 chapter 标签
  3. 按「x.x.x」切分条文，分款（"1 …""2 …"）归入同一条
  4. 修补 PDF 换行造成的断裂
  5. 从用词推断 severity_hint（应/必须/不得 → error；宜 → warning）
  6. 从节标题和数值约束里抽 keywords

不负责的部分：interpret（人话解读）、machine（可执行约束）。这两项要人读进去写，
脚本留空，并在 source_note 里标明待补。

用法:
    python scripts/ingest_pdf.py gb55037.pdf --standard GB55037-2022 \\
        --name 建筑防火通用规范 --source-url https://... -o data/kb/gb55037-2022.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf  # PyMuPDF 2.x；旧版导入名是 fitz
import yaml

# 第三方转载站嵌进 PDF 的页眉水印；整行丢弃，不影响正文
NOISE_PATTERNS = [
    r"1190119\.com",
    r"1190119\.com",
    r"消防大讲堂",
    r"消防资源网",
    r"^\s*\d{1,3}\s*$",           # 孤立页码
]

CLAUSE_RE = re.compile(r"^(\d{1,2}\.\d{1,2}\.\d{1,2})\s*(.*)$")
SECTION_RE = re.compile(r"^(\d{1,2}\.\d{1,2})\s+([^\s.].*)$")   # 两位.两位，且后面不是点线
# 章标题：「1 总则」「4 建筑平面布置与防火分隔」。必须是纯中文短标题，
# 否则会把分款（「4 邮袋库、丝麻棉毛类物质库。」）误认成章标题
CHAPTER_RE = re.compile(r"^(\d{1,2})\s+([\u4e00-\u9fa5、，]{2,20})$")
ITEM_RE = re.compile(r"^(\d{1,2})\s+(?=\S)")
TOC_DOTS = re.compile(r"[.．]{4,}")

# ---- 用词 → 严重程度 ----------------------------------------------------
# "相应/响应/适应/供应/对应/反应" 里的「应」不是规范用词，先替换掉再统计
_NOT_MUST = re.compile(r"[相响适供对反]应")
_NOT_SHOULD = re.compile(r"[适便]宜")


def infer_severity(text: str) -> str:
    t = _NOT_MUST.sub("x", text)
    t = _NOT_SHOULD.sub("y", t)
    if re.search(r"应|必须|不得|严禁|禁止", t):
        return "error"
    if re.search(r"宜", t):
        return "warning"
    return "info"


NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(m²|m2|㎡|mm|m|h|min|s|℃|%|层|人|部|个|处)")


def extract_keywords(text: str, section: str, limit: int = 8) -> list[str]:
    """节标题 + 数值约束作为检索锚点。

    不做分词：中文术语切分需要词典，切错反而污染检索。节标题本身是稳定的
    主题词（如「安全疏散」），数值约束则是精确锚点（如「1.0m」）。
    「一般规定」这类通用节标题不带信息量，跳过。
    """
    out: list[str] = []
    title = re.sub(r"^\d{1,2}\.\d{1,2}\s*", "", section).strip()
    if title and title not in GENERIC_SECTIONS:
        out.append(title)
    for m in NUM_RE.finditer(text):
        token = f"{m.group(1)}{m.group(2)}"
        if token not in out:
            out.append(token)
    return out[:limit]


GENERIC_SECTIONS = {"一般规定", "基本规定", "总则", "其他工程", "其他规定"}


def is_noise(line: str) -> bool:
    return any(re.search(p, line) for p in NOISE_PATTERNS)


def extract_lines(pdf_path: Path) -> list[str]:
    doc = pymupdf.open(pdf_path)
    lines: list[str] = []
    for page in doc:
        for raw in page.get_text().split("\n"):
            s = raw.strip()
            if not s or is_noise(s):
                continue
            lines.append(s)
    doc.close()
    return lines


def _cur_chapter_no(chapter: str) -> int:
    """从 chapter 串（如「8 火灾自动报警系统」）取章号，空章或无号返回 0。"""
    if not chapter:
        return 0
    head = chapter.split(" ", 1)[0]
    return int(head) if head.isdigit() else 0


def join_cjk(parts: list[str]) -> str:
    """把被 PDF 换行切断的正文拼回去。中文不补空格，ASCII 单词之间补。"""
    out = ""
    for p in parts:
        if not out:
            out = p
            continue
        if out[-1].isascii() and out[-1].isalnum() and p[0].isascii() and p[0].isalnum():
            out += " " + p
        else:
            out += p
    return out


def _same_segment(a: str, b: str) -> bool:
    """两段文字是不是同一段被 PDF 重复印了两遍。

    重复印刷的两版未必逐字相同——字库替换会掉个别字（「大于」印成「大」或「天于」）。
    所以除了前缀关系，还要按相似度兜一层。
    """
    if a.startswith(b) or b.startswith(a):
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.9


def _is_cross_reference(rest: str) -> bool:
    """判断这一行是"条文编号"还是"跨行引用"。

    PDF 排版会把引用断在两行，第二行看起来就是一个以条文号开头的新条文。
    真实出现过的两种形态：
        …应符合本规范第\\n7.1.8 条的规定；…      → 条文号引用，rest 以「条」开头
        …不应小于表\\n7.4.7 的规定值。          → 表格号引用，rest 以「的」开头
    真正的条文编号后面跟的是条文正文，不会以「条」「的」开头。

    这个坑真实发生过：2.2.8 被切成两半，还伪造出一条假的 7.1.8，
    而真正的 7.1.8（室内疏散楼梯间）因为 id 重复被去重逻辑静默丢掉了。
    """
    return rest.lstrip().startswith(("条", "的"))


def split(lines: list[str]) -> tuple[list[dict], list[str]]:
    """返回 (clauses, preamble)。preamble 是第一条之前的公告与目次。

    正文起点必须单独定位：公告里的"同时废止下列标准相关强制性条文"清单也含
    「第4.1.5、4.2.9」这类编号，若直接按条文号切分，清单会被误当成条文
    （曾把一条废止清单切成了 3.2.1）。总则的第一条恒为 1.0.1 或 1.1.1，
    以它为界，之前全部归 preamble。
    """
    start = 0
    for i, ln in enumerate(lines):
        m = CLAUSE_RE.match(ln)
        if m and m.group(1).startswith("1."):
            # 1.0.1 前面紧跟着「1 总则」章标题，要一起纳入，否则总则 9 条没有 chapter
            start = i
            for j in range(i - 1, max(-1, i - 20), -1):
                if CHAPTER_RE.match(lines[j]):
                    start = j
                    break
            break
    preamble_lines = lines[:start]
    lines = lines[start:]

    clauses: list[dict] = []
    chapter = ""
    section = ""
    cur: dict | None = None

    for ln in lines:
        if TOC_DOTS.search(ln):        # 目次行，跳过
            continue
        mch = CHAPTER_RE.match(ln)
        # 章号必须正好是当前章 +1，否则视为正文。正文里偶尔出现的
        # 「3 某某场所」这类行不会被误认成新章（GB55037 第 9 章标题
        # 「9 供暖、通风和空气调节系统」含顿号，早先漏匹配后被并进了 8.3.3 的正文）。
        if mch and int(mch.group(1)) == _cur_chapter_no(chapter) + 1:
            if cur:
                clauses.append(cur)
                cur = None
            chapter = f"{mch.group(1)} {mch.group(2)}"
            section = chapter        # 新章开头的条文（如 1.0.1）以章名为 chapter
            continue
        ms = SECTION_RE.match(ln)
        if ms:                          # 节标题：切节
            if cur:
                clauses.append(cur)
                cur = None
            section = f"{ms.group(1)} {ms.group(2)}".strip()
            continue
        mc = CLAUSE_RE.match(ln)
        if mc and not _is_cross_reference(mc.group(2)):
            if cur:
                clauses.append(cur)
            cur = {"clause_id": mc.group(1), "section": section, "raw": [mc.group(2)] if mc.group(2) else []}
        elif cur is not None:
            cur["raw"].append(ln)
        else:
            preamble_lines.append(ln)   # 第一条之前、或节标题与首条之间
    if cur:
        clauses.append(cur)
    return clauses, preamble_lines


def normalize(raw: dict) -> dict:
    """正文与分款分开保存：分款在 YAML 里各占一行，读起来接近原文。"""
    body: list[str] = []
    items: list[str] = []
    for ln in raw["raw"]:
        if ITEM_RE.match(ln):
            items.append(ln)
        elif items:
            items[-1] += ln         # 分款跨行，续到上一条
        else:
            body.append(ln)

    verbatim = join_cjk(body)
    if items:
        verbatim += "\n" + "\n".join(items)

    return {
        "clause_id": raw["clause_id"],
        "chapter": raw["section"],
        "verbatim": verbatim,
        "severity_hint": infer_severity(verbatim),
        "keywords": extract_keywords(verbatim, raw["section"]),
    }


def extract_effective_from(preamble: str) -> str:
    m = re.search(r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日起\s*实施", preamble)
    if not m:
        return ""
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


class _Dumper(yaml.SafeDumper):
    """让多行字符串输出成 | 块标量，而不是被折成一行加引号。"""


def _str_rep(dumper, data: str):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_rep)


def main() -> int:
    ap = argparse.ArgumentParser(description="国标 PDF → 条文 YAML")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--standard", required=True, help="如 GB55037-2022")
    ap.add_argument("--name", default="", help="规范中文名")
    ap.add_argument("--source-url", default="", help="PDF 所在页面（不是 PDF 直链）")
    ap.add_argument("--note", default="", help="写进 source_note 的说明")
    ap.add_argument("-o", "--output", type=Path, default=None)
    args = ap.parse_args()

    lines = extract_lines(args.pdf)
    raw_clauses, preamble = split(lines)
    norm = [normalize(c) for c in raw_clauses]

    seen_at: dict[str, int] = {}
    unique: list[dict] = []
    page_dupes: list[str] = []
    real_dupes: list[str] = []
    for c in norm:
        cid = c["clause_id"]
        # 加标准前缀：裸的「1.0.1」在不同标准之间会撞车（多条规范都有 1.0.1）
        c["clause_id"] = f"{args.standard}/{cid}"
        if cid in seen_at:
            old = unique[seen_at[cid]]
            if _same_segment(old["verbatim"], c["verbatim"]):
                # 换页拼接会把同一段文字重复印一遍（GB 55037 的 PDF 里真实存在 9 处），
                # 保留更长的那条；长度相同则保留先出现的。——这是数据源问题，不是切分问题。
                if len(c["verbatim"]) > len(old["verbatim"]):
                    unique[seen_at[cid]] = c
                page_dupes.append(cid)
            else:
                real_dupes.append(cid)
            continue
        seen_at[cid] = len(unique)
        unique.append(c)

    if page_dupes:
        print(f"PDF 中有 {len(page_dupes)} 段重复排版，已保留较长版本：", file=sys.stderr)
        for cid in dict.fromkeys(page_dupes):
            print("  ", cid, file=sys.stderr)
    # 条文号重复且内容不同，一定是切分出错，宁可失败也不产出带脏数据的库
    if real_dupes:
        print(f"发现 {len(real_dupes)} 个条文号重复且内容不同，切分有误，拒绝写入：", file=sys.stderr)
        for cid in dict.fromkeys(real_dupes):
            print("  ", cid, file=sys.stderr)
        return 1

    pre = join_cjk(preamble)
    note = args.note or "verbatim 由 PDF 机械切分；interpret / machine 待人工补全。"
    doc = {
        "standard": args.standard,
        "standard_name": args.name,
        "effective_from": extract_effective_from(pre),
        "source_url": args.source_url,
        "source_note": note,
        "clauses": unique,
    }

    text = yaml.dump(doc, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=100)
    if args.output:
        args.output.write_text(text, encoding="utf-8")

    counts: dict[str, int] = {}
    for c in unique:
        counts[c["severity_hint"]] = counts.get(c["severity_hint"], 0) + 1
    print(f"正文行 {len(lines)}｜条文 {len(unique)}（切分得到 {len(norm)}）", file=sys.stderr)
    print(f"severity_hint 分布 {counts}", file=sys.stderr)
    print(f"含分款的条文 {sum(1 for c in unique if chr(10) in c['verbatim'])}", file=sys.stderr)
    if args.output:
        print(f"已写入 {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
