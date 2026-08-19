"""epub → 扁平文本 + 段落区间 + 章节索引。

**position 用字符偏移而非段号**：合并阈值一改，段号的含义就变，旧进度会漂到错误
位置；字符偏移对分页/合并参数免疫。`raw_starts` 只为从 v1 的 `seg` 段号迁移而保留。

三个来自实测的解析要点（换书时容易再踩）：
  1. 正文既在 `<p>` 也在 `<blockquote>` —— 三体英译本第 13 章 89 个 blockquote / 7 个
     p，只取 p 会整章丢失。取两种标签并按 iterancestors() 去掉嵌套重复。
  2. `<1500 字节` 的 spine item 是脚注页/版权页（实测 42 个），丢掉；但 toc.ncx 目录项
     落在这类文件上时必须沿 spine 向后顺延到下一个有正文的文件，否则 Part 分隔会丢。
  3. 章节标题同时出现在 ncx 和正文 `<p>` 里（实测 20 处），正文侧要滤掉 —— 否则一次
     按键只出 "1. The Madness Years" 这 20 个字符。
"""
import os
import re
import zipfile
from dataclasses import dataclass

from lxml import etree, html as lhtml

MIN_BLOCK = 20      # 短于此的 <p> 是页眉/页码/装饰符
MIN_FILE = 1500     # 小于此字节的 spine item 视为非正文页

# **排版用的不可见字符必须删掉，不能留。** epub 里软连字符（U+00AD，只在断行处才显示为
# 连字符）铺得极密 —— Ball Lightning 一本就有 7438 个。留着的话它渲染出来零宽、屏幕上
# 什么都看不见，但打字模式的光标会停在它上面：你敲下一个看得见的字符不匹配，游标不动
# （严格前进），表现就是"这本书打到某个词就卡死"。同理清掉零宽空格等其它零宽字符。
_DROP = {ord(c): None for c in "­​‌‍⁠﻿"}

_TR = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                     " ": " ", "–": "-"})


def norm(s):
    """一级归一化 + 二级展开，覆盖实测非 ASCII 字符的 95% —— 打字模式才敲得出来。"""
    return s.translate(_DROP).translate(_TR).replace("—", "--").replace("…", "...")


@dataclass
class Book:
    path: str
    text: str          # 全书扁平文本，段落间 "\n\n"
    blocks: list       # [(start, end)] 原始段落区间，已滤掉章节标题行
    chapters: list     # [(offset, label)] 按 offset 升序
    raw_starts: list   # v1 段号 → 偏移；仅供进度迁移，其它地方不要用

    @property
    def key(self):
        return os.path.basename(self.path)

    @property
    def title(self):
        return re.sub(r"\.epub$", "", self.key, flags=re.I)

    def chapter_at(self, off):
        """(序号, offset, 标签) —— 最后一个起始偏移 <= off 的章节；无章节时 None。"""
        lo = None
        for n, (start, label) in enumerate(self.chapters):
            if start <= off:
                lo = (n, start, label)
            else:
                break
        return lo


def _spine_docs(z, opf, base):
    """按 spine 顺序产出 (href, 原始字节)，跳过读不到的和过小的。

    spine 是阅读顺序的权威来源 —— manifest 的字典序、文件名里的数字都不可靠。
    """
    man = {e.get("id"): (e.get("href"), e.get("media-type") or "")
           for e in opf.iter("{*}item")}
    for ref in opf.iter("{*}itemref"):
        href = (man.get(ref.get("idref")) or (None, None))[0]
        if not href:
            continue
        try:
            raw = z.read(os.path.normpath(os.path.join(base, href)))
        except KeyError:
            yield href, None                 # 仍要占 spine 位置，供顺延用
            continue
        yield href, raw if len(raw) >= MIN_FILE else None


def _blocks_in(raw):
    """抽一个 xhtml 文档里的正文块。嵌套块只取最外层，否则正文会重复一遍。"""
    for el in lhtml.fromstring(raw).iter("p", "blockquote"):
        if any(a.tag in ("p", "blockquote") for a in el.iterancestors()
               if isinstance(a.tag, str)):
            continue
        t = norm(" ".join(el.text_content().split()))
        if len(t) >= MIN_BLOCK:
            yield t


def _toc_labels(z, opf, base, spine, first):
    """toc.ncx → ({全书块序号: 标题}, 全部标题集合)。

    目录项落在被过滤掉的小文件上时（分部标题页、版权页），沿 spine 向后顺延到第一个
    有正文的文件。同一序号上后来的标签覆盖先前的 —— "1. The Madness Years" 比
    "Part I" 更适合做跳转标签。

    第二个返回值是**覆盖前**的全部标签：被覆盖掉的那些（如 "Part I: Silent Spring"）
    照样以正文段落的形式出现在书里，滤重时必须认得它们。
    """
    man = {e.get("id"): (e.get("href"), e.get("media-type") or "")
           for e in opf.iter("{*}item")}
    ncx = next((h for h, mt in man.values() if "dtbncx" in mt), None)
    if not ncx:
        return {}, set()
    out, seen = {}, set()
    toc = etree.fromstring(z.read(os.path.normpath(os.path.join(base, ncx))))
    for np in toc.iter("{*}navPoint"):
        label = np.find("{*}navLabel/{*}text")
        text = norm(" ".join(" ".join(label.itertext()).split())) if label is not None else ""
        if text:
            seen.add(text)
        src = (np.find("{*}content").get("src") or "").split("#")[0]
        if src in spine:
            idx = next((first[h] for h in spine[spine.index(src):] if h in first), None)
        else:
            idx = first.get(src)
        if idx is not None:
            out[idx] = text
    return out, seen


def load(path):
    """解析 epub 为 Book。两遍：先按 spine 抽块并记住每文件首块序号，再关联 ncx 标签。"""
    z = zipfile.ZipFile(path)
    opf_path = re.search(rb'full-path="([^"]+)"',
                         z.read("META-INF/container.xml")).group(1).decode()
    opf, base = etree.fromstring(z.read(opf_path)), os.path.dirname(opf_path)

    raw_blocks, spine, first = [], [], {}    # first: href → 该文件第一块的序号
    for href, raw in _spine_docs(z, opf, base):
        spine.append(href)
        if raw is None:
            continue
        for t in _blocks_in(raw):
            first.setdefault(href, len(raw_blocks))
            raw_blocks.append(t)

    labels, titles = _toc_labels(z, opf, base, spine, first)

    # 章节标题在 ncx 和正文 <p> 里各出现一次（实测 20 处），正文侧滤掉。比较时去掉全部
    # 空白 —— ncx 写 "Three Body : King Wen" 而正文写 "Three Body: King Wen"，只差一个
    # 空格。反过来，研究报告里的真小标题（"1. Current International Research Trends"）
    # 不在 ncx 里，不会被误删。
    squeezed = {re.sub(r"\s+", "", t) for t in titles}
    text, blocks, chapters, raw_starts = [], [], [], []
    pos = 0
    for n, t in enumerate(raw_blocks):
        if n in labels:
            chapters.append((pos, labels[n]))
        raw_starts.append(pos)
        if re.sub(r"\s+", "", t) in squeezed:
            continue
        text.append(t)
        blocks.append((pos, pos + len(t)))
        pos += len(t) + 2                    # 段落间 "\n\n"

    return Book(path=path, text="\n\n".join(text), blocks=blocks,
                chapters=sorted(chapters), raw_starts=raw_starts)
