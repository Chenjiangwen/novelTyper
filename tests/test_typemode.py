"""打字模式的三条规则 —— 用注入的 read_key，不需要 pty。

第一条测试就是最早那个 bug 的回归：连按空格不能翻页。
"""
import re

import pytest

from noveltyper import typemode

STRIP = re.compile(r"\033\[[0-9;]*[A-Za-z]")


def feeder(keys):
    it = iter(keys)
    return lambda: next(it, "\x03")        # 喂完就当放弃，避免测试卡死


def test_wrong_keys_never_advance(capsys):
    """连按 400 次空格必须打不完 —— 严格前进的回归测试。"""
    target = "The clothes she was wearing were dry."
    r = typemode.run(feeder([" "] * 400), [target], "x.txt", "$ ")
    assert r is None                       # 400 次之后被喂了 \x03 → 放弃


def test_correct_typing_completes(capsys):
    target = "abc def"
    r = typemode.run(feeder(list(target)), [target], "x.txt", "$ ")
    assert r is not None
    wpm, acc = r
    assert acc == 100.0 and wpm > 0


def test_accuracy_counts_mistakes(capsys):
    target = "abc"
    keys = ["a", "x", "b", "c"]            # 一次错
    r = typemode.run(feeder(keys), [target], "x.txt", "$ ")
    assert r is not None
    assert r[1] == pytest.approx(75.0)


def test_frame_shows_target_not_input():
    """错字时屏幕上仍是原文该出现的字符 —— 正文不能被手误改写。"""
    frame = typemode._frame("abc", 1, bad=True)
    assert "b" in STRIP.sub("", frame)
    plain = STRIP.sub("", frame)
    assert plain == "abc"                  # 宽度恒定，且逐字对应原文


def test_frame_width_constant():
    target = "hello world"
    for pos in range(len(target) + 1):
        assert STRIP.sub("", typemode._frame(target, pos, False)) == target


def test_unreachable_chars_skipped(capsys):
    """键盘敲不出的字符自动跳过，不该成为路障。"""
    target = "a©b"
    r = typemode.run(feeder(["a", "b"]), [target], "x.txt", "$ ")
    assert r is not None and r[1] == 100.0


def test_backspace_rewinds_without_penalty(capsys):
    target = "ab"
    r = typemode.run(feeder(["a", "\x7f", "a", "b"]), [target], "x.txt", "$ ")
    assert r is not None and r[1] == 100.0


def test_escape_aborts(capsys):
    r = typemode.run(feeder(["a", "\x1b"]), ["abc"], "x.txt", "$ ")
    assert r is None


def test_escape_sequence_ignored(capsys):
    """方向键在打字模式里也不能被当成击键。"""
    r = typemode.run(feeder([None, None, "a", "b"]), ["ab"], "x.txt", "$ ")
    assert r is not None and r[1] == 100.0
