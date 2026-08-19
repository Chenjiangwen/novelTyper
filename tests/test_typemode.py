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


def test_skip_covers_every_non_ascii_char_not_just_a_whitelist():
    """**跳过判据必须是值域而不是白名单。**

    `read_key` 一次 `os.read` 只取一个字节，永远只返回 ASCII，所以任何 ord>126 的字符
    都匹配不上、停在它上面就是死锁。原实现用手写字符表 `SAFE`，漏掉的字符成了看不见的
    路障 —— 实测 Ball Lightning 有 7438 个软连字符（零宽，屏幕上看不出异常），敲什么都
    不前进。这条钉住那次回归：抽样覆盖软连字符、零宽空格、汉字、西里尔、带调符拉丁。
    """
    for ch in "­​ìīāǎÉ½刘白е©§":
        assert not typemode.typeable(ch), repr(ch)
        assert typemode._skip(f"a{ch}b", 1) == 2, repr(ch)
    # 控制字符同样是死锁：主循环把 ord<32 当控制键忽略，不跳过就永远等不到匹配。
    for ch in "\t\n\r\x00\x1f":
        assert not typemode.typeable(ch), repr(ch)
        assert typemode._skip(f"a{ch}b", 1) == 2, repr(ch)
    for ch in "azAZ09 .,'\"-!?":
        assert typemode.typeable(ch), repr(ch)


def test_soft_hyphen_mid_word_does_not_stall(capsys):
    """用户实际卡住的那一句：`the ­whole` 之间夹一个软连字符，按 w 没反应。"""
    target = "as if the ­whole universe"
    typed = [c for c in target if typemode.typeable(c)]
    r = typemode.run(feeder(typed), [target], "x.txt", "$ ")
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


def test_say_is_called_once_per_line(capsys):
    """**按行朗读，不是按整段。** 整段读完要十几秒，人早打到第三行了，声音和光标对不上
    就成了干扰。这条钉住粒度：每行进入时恰好念一次它自己。"""
    said = []
    lines = ["first line", "second"]
    keys = list("first line") + list("second")
    r = typemode.run(feeder(keys), lines, "x.txt", "$ ", say=said.append)
    assert r is not None
    assert said == lines


def test_replay_does_not_count_as_keystroke(capsys):
    """Ctrl-R 重听不算击键、不动游标 —— 否则听不清多按两下就把准确率打下去了。"""
    said = []
    r = typemode.run(feeder(["a", "\x12", "\x12", "b"]), ["ab"], "x.txt", "$ ",
                     say=said.append)
    assert r is not None and r[1] == 100.0      # 两次重听没记成错字
    assert said == ["ab", "ab", "ab"]           # 进入时一次 + 两次重听


def test_replay_without_say_is_harmless(capsys):
    """没开朗读时按 Ctrl-R 不能炸、也不能吃掉后面的输入。"""
    r = typemode.run(feeder(["\x12", "a", "b"]), ["ab"], "x.txt", "$ ")
    assert r is not None and r[1] == 100.0
