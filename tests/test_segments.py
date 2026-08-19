"""segments.py：合并不能吞字、不能跨章、不能超顶。"""
from noveltyper import segments


def _mk(text, sizes):
    """按 sizes 生成 blocks（段间两空格位），返回 (text, blocks)。"""
    blocks, pos, parts = [], 0, []
    for n in sizes:
        parts.append("x" * (n - 1) + ".")
        blocks.append((pos, pos + n))
        pos += n + 2
    return "\n\n".join(parts), blocks


def test_no_text_lost_or_duplicated():
    text, blocks = _mk(None, [50, 60, 70, 900, 30])
    units = segments.build(blocks, set(), text)
    # 每个原段的起点都必须落在某个单元内部（覆盖性），且单元不重叠。
    for s, e in zip(units, units[1:]):
        assert s[1] <= e[0]
    covered = [(a, b) for a, b in units]
    assert covered[0][0] == blocks[0][0]
    assert covered[-1][1] == blocks[-1][1]


def test_hard_cap_respected():
    text, blocks = _mk(None, [3000])
    units = segments.build(blocks, set(), text)
    assert len(units) > 1
    assert all(e - s <= segments.HARD_CAP for s, e in units)


def test_never_crosses_chapter():
    text, blocks = _mk(None, [40, 40, 40, 40])
    chapters = {blocks[2][0]}
    units = segments.build(blocks, chapters, text)
    starts = {s for s, _ in units}
    assert blocks[2][0] in starts


def test_merging_reduces_short_units():
    text, blocks = _mk(None, [30] * 40)
    units = segments.build(blocks, set(), text)
    assert len(units) < len(blocks)
    assert all(e - s >= segments.TARGET or i == len(units) - 1
               for i, (s, e) in enumerate(units))


def test_sentence_cut_prefers_punctuation():
    body = "a" * 300 + ". " + "b" * 500
    cut = segments._sentence_cut(body, segments.TRUNC)
    assert body[cut - 1] == "."


def test_sentence_cut_falls_back():
    body = "a" * 800                      # 没有任何标点
    cut = segments._sentence_cut(body, segments.TRUNC)
    assert cut == segments.TRUNC


def test_index_at():
    units = [(0, 10), (12, 30), (32, 50)]
    assert segments.index_at(units, 0) == 0
    assert segments.index_at(units, 11) == 0     # 落在缝里 → 归前一个
    assert segments.index_at(units, 20) == 1
    assert segments.index_at(units, 999) == 2
    assert segments.index_at(units, -5) == 0


def test_real_book_distribution(real_book):
    b = real_book
    units = segments.build(b.blocks, {o for o, _ in b.chapters}, b.text)
    lens = [e - s for s, e in units]
    assert len(units) < len(b.blocks)
    assert max(lens) <= segments.HARD_CAP
    assert sum(1 for n in lens if n < 80) / len(lens) < 0.05
    starts = {s for s, _ in units}
    assert all(o in starts for o, _ in b.chapters)   # 章节跳转必须落在单元起点
