"""四主题的视觉不变量：正文前缀等宽、不超宽、渲染确定性。"""
import re

import pytest

from noveltyper import themes

STRIP = re.compile(r"\033\[[0-9;]*m")

TEXT = ("Ye discovered that she was covered by a military coat as well. The "
        "clothes she was wearing were dry and warm. She struggled to sit up, "
        "and to her surprise, succeeded.")


def ctx(cols=100, i=162, off=54321):
    return themes.Ctx(i=i, total=1857, text=TEXT, chapter="3. Red Coast I",
                      cols=cols, ps1="dev@mac corpus % ", offset=off, seed=off)


@pytest.mark.parametrize("theme", themes.ALL, ids=lambda t: t.key)
@pytest.mark.parametrize("cols", [48, 72, 100])
def test_body_never_overflows(theme, cols):
    """**正文行**在任何宽度下都不能超宽 —— 超了就要靠终端软换行，缩进会错乱。"""
    lines = [STRIP.sub("", l) for l in theme.body(ctx(cols=cols))]
    assert lines and all(len(l) <= cols for l in lines)


@pytest.mark.parametrize("theme", themes.ALL, ids=lambda t: t.key)
@pytest.mark.parametrize("cols", [72, 100])
def test_whole_render_fits_at_usable_widths(theme, cols):
    """噪音行按真实工具的行为写死（pytest 的断言行就是不看终端宽度的），只保证在
    常用宽度下整屏不折行；48 列是 `term.cols` 的下限兜底，不是使用场景。"""
    out = STRIP.sub("", themes.render(theme, ctx(cols=cols)))
    over = [l for l in out.split("\n")[1:] if len(l) > cols]
    assert not over, over


@pytest.mark.parametrize("theme", themes.ALL, ids=lambda t: t.key)
@pytest.mark.parametrize("cols", [72, 100])
def test_no_overflow_across_many_offsets(theme, cols):
    """扫一批偏移 —— 超宽往往来自 offset 派生的数字或 `workspace.pick` 选中的长符号名，
    单个 offset 测不出来（rg 主题的长测试函数名就是这么漏过去的）。"""
    for off in range(0, 200_000, 1301):
        out = STRIP.sub("", themes.render(theme, ctx(cols=cols, i=off // 300, off=off)))
        over = [l for l in out.split("\n")[1:] if len(l) > cols]
        assert not over, (off, over)


@pytest.mark.parametrize("theme", themes.ALL, ids=lambda t: t.key)
def test_body_prefix_is_fixed_width(theme):
    lines = [STRIP.sub("", l) for l in theme.body(ctx())]
    assert len(lines) > 1
    widths = {len(l) - len(l.lstrip()) if theme.prefix.strip() == "" else None
              for l in lines}
    # 逐行还原前缀：正文行去掉前缀后应拼回原文（顺序、内容都不能变）。
    plain = " ".join(l[len(theme.prefix):].strip() for l in lines)
    assert plain.replace("  ", " ") == TEXT.replace("  ", " ")
    assert all(len(l) >= len(theme.prefix) for l in lines)


@pytest.mark.parametrize("theme", themes.ALL, ids=lambda t: t.key)
def test_deterministic(theme):
    a = themes.render(theme, ctx())
    b = themes.render(theme, ctx())
    # 只有 git 主题带真实时间戳，其它必须逐字节相同。
    if theme.key != "git":
        assert a == b
    else:
        assert STRIP.sub("", a).split("\n")[1] == STRIP.sub("", b).split("\n")[1]


@pytest.mark.parametrize("theme", themes.ALL, ids=lambda t: t.key)
def test_text_present_verbatim(theme):
    """正文必须逐词出现 —— 伪装不能吃掉或改写内容。"""
    out = STRIP.sub("", themes.render(theme, ctx()))
    flat = " ".join(out.split())
    for word in ("discovered", "military", "succeeded"):
        assert word in flat


def test_git_hunk_header_matches_body():
    out = STRIP.sub("", themes.render(themes.BY_KEY["git"], ctx()))
    m = re.search(r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@", out)
    assert m
    added = sum(1 for l in out.split("\n") if l.startswith("+") and
                not l.startswith("+++"))
    ctxlines = sum(1 for l in out.split("\n")
                   if l.startswith(" ") and "Reviewed by" in l or l == " ")
    assert int(m.group(4)) == added + ctxlines
    assert int(m.group(2)) == ctxlines


def test_ripgrep_line_numbers_increase():
    lines = [STRIP.sub("", l) for l in themes.BY_KEY["rg"].body(ctx())]
    nums = [int(l.split(":")[0]) for l in lines]
    assert nums == sorted(nums) and len(set(nums)) == len(nums)
    assert all(len(l.split(":")[0]) == 5 for l in lines)      # 定宽 %5d


def test_theme_cycle():
    keys = [t.key for t in themes.ALL]
    k = keys[0]
    seen = []
    for _ in range(len(keys)):
        seen.append(k)
        k = themes.next_key(k)
    assert seen == keys and k == keys[0]
    assert themes.next_key("nonsense") == keys[0]
    assert themes.get("nonsense") is themes.ALL[0]


@pytest.mark.parametrize("theme", themes.ALL, ids=lambda t: t.key)
def test_toc_row_fits(theme):
    row = STRIP.sub("", theme.toc_row(3, "1. The Madness Years", 12345, 100, True))
    assert len(row) <= 100 and "Madness" in row


def test_elapsed_monotonic():
    assert ctx(i=0).elapsed < ctx(i=100).elapsed < ctx(i=1000).elapsed
