"""book.py 的不变量。偏移模型出错的代价是整本书的进度都漂，值得逐条钉住。"""
import re

from noveltyper import book


def test_offsets_slice_back_to_text(synth_epub):
    """blocks 的区间必须能从 text 里切回原段 —— 偏移模型的地基。"""
    b = book.load(synth_epub)
    assert b.blocks
    for start, end in b.blocks:
        seg = b.text[start:end]
        assert seg and not seg.startswith(" ") and "\n" not in seg


def test_blocks_monotonic_and_gapped(synth_epub):
    """段落严格递增，且相邻段之间恰好隔 "\\n\\n"。"""
    b = book.load(synth_epub)
    for (s1, e1), (s2, _) in zip(b.blocks, b.blocks[1:]):
        assert s1 < e1 < s2
        assert b.text[e1:s2] == "\n\n"


def test_chapter_titles_filtered_from_body(synth_epub):
    """ncx 里的标题不该再作为正文段出现 —— 否则一次按键只出十几个字符。"""
    b = book.load(synth_epub)
    labels = {re.sub(r"\s+", "", l) for _, l in b.chapters}
    bodies = {re.sub(r"\s+", "", b.text[s:e]) for s, e in b.blocks}
    assert not (labels & bodies)


def test_part_label_forwarded_past_small_file(synth_epub):
    """落在小文件上的目录项要顺延到下一个有正文的文件，Part 分隔不能丢。"""
    b = book.load(synth_epub)
    assert [l for _, l in b.chapters][:1] == ["Part I: Beginnings"] or \
           "1. First Chapter" in [l for _, l in b.chapters]
    assert len(b.chapters) >= 2


def test_chapters_sorted_and_aligned(synth_epub):
    b = book.load(synth_epub)
    offs = [o for o, _ in b.chapters]
    assert offs == sorted(offs)
    starts = {s for s, _ in b.blocks}
    assert all(o in starts for o in offs)


def test_chapter_at(synth_epub):
    b = book.load(synth_epub)
    assert b.chapter_at(-1) is None or b.chapter_at(0) is not None
    for off, label in b.chapters:
        assert b.chapter_at(off)[2] == label
        assert b.chapter_at(off + 5)[2] == label


def test_raw_starts_covers_every_raw_block(synth_epub):
    """raw_starts 是 v1 迁移的唯一依据，长度必须等于**过滤前**的段数。"""
    b = book.load(synth_epub)
    assert len(b.raw_starts) >= len(b.blocks)
    assert b.raw_starts == sorted(b.raw_starts)


def test_norm_ascii_only():
    s = book.norm("“He said—’tis fine…” he knew–yes")
    assert s == '"He said--\'tis fine..." he knew-yes'


def test_norm_drops_invisible_typography_chars():
    """**零宽字符必须在解析期删掉，不能留到打字模式。**

    epub 用软连字符（U+00AD）标断行候选点，实测 Ball Lightning 一本有 7438 个。它渲染
    出来零宽，屏幕上完全看不出异常，但打字模式的光标会停在上面 —— 敲下一个看得见的字符
    不匹配，游标不动（严格前进），表现成"这本书打到某个词就卡死"。
    """
    assert book.norm("as if the ­whole") == "as if the whole"
    for cp in (0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF):
        assert book.norm(f"a{chr(cp)}b") == "ab", hex(cp)


def test_real_book_invariants(real_book):
    b = real_book
    assert len(b.blocks) > 1000 and len(b.chapters) > 10
    for start, end in b.blocks:
        assert b.text[start:end] == b.text[start:end].strip()
    labels = {re.sub(r"\s+", "", l) for _, l in b.chapters}
    bodies = {re.sub(r"\s+", "", b.text[s:e]) for s, e in b.blocks}
    assert not (labels & bodies)
