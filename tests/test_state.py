"""进度迁移与原子写。v1 → v2 换算错了会把进度扔到书的开头，必须钉住。"""
import json

from noveltyper import panic, state, term, workspace


class FakeBook:
    key = "The Three-Body Problem (Cixin Liu).epub"
    text = "x" * 100_000
    raw_starts = [i * 300 for i in range(1500)]


def test_v1_seg_migrates_via_raw_starts():
    st = {FakeBook.key: {"seg": 123, "typed": 0, "wpm": 0.0}}
    rec = state.record(st, FakeBook())
    assert rec["offset"] == FakeBook.raw_starts[123]
    assert rec["migrated_from_seg"] == 123


def test_offset_wins_over_seg():
    st = {FakeBook.key: {"seg": 123, "offset": 999}}
    assert state.record(st, FakeBook())["offset"] == 999


def test_offset_clamped_to_text():
    st = {FakeBook.key: {"offset": 10 ** 9}}
    assert state.record(st, FakeBook())["offset"] < len(FakeBook.text)


def test_missing_record_defaults():
    """新书要拿到一整套默认值 —— 主循环直接下标取，缺一个键就是 KeyError。

    断言逐键而不是整字典相等：加一个可持久化的偏好（tts / voice 就是这么来的）不该
    让这条测试失败，它要钉的是「默认值对不对」，不是「有几个键」。
    """
    rec = state.record({}, FakeBook())
    assert rec["offset"] == 0 and rec["typed"] == 0 and rec["wpm"] == 0.0
    assert rec["tts"] is False and rec["voice"] == ""


def test_tts_prefs_round_trip():
    """朗读开关和后端要跟着书存 —— 一本书听读、另一本默读是常态。"""
    st = state.update({}, FakeBook(), 100, 0, 0.0, "git", tts=True, voice="edge")
    assert state.record(st, FakeBook())["tts"] is True
    assert state.record(st, FakeBook())["voice"] == "edge"


def test_update_leaves_tts_alone_when_not_passed():
    """不传就不动 —— 老调用方（和 v1 存档迁移路径）不该被顺手清掉偏好。"""
    st = state.update({}, FakeBook(), 100, 0, 0.0, "git", tts=True, voice="edge")
    st = state.update(st, FakeBook(), 200, 0, 0.0, "git")
    assert st[FakeBook.key]["tts"] is True and st[FakeBook.key]["voice"] == "edge"


def test_update_drops_seg():
    st = {FakeBook.key: {"seg": 5}}
    st = state.update(st, FakeBook(), 900, 3, 41.27, "git")
    rec = st[FakeBook.key]
    assert "seg" not in rec
    assert rec == {"offset": 900, "typed": 3, "wpm": 41.3, "theme": "git"}
    assert st["_v"] == state.VERSION


def test_save_is_atomic_and_leaves_no_temp(tmp_path):
    p = tmp_path / "sub" / "progress.json"
    state.save({"a": {"offset": 1}}, p)
    assert json.loads(p.read_text())["a"]["offset"] == 1
    assert [f.name for f in p.parent.iterdir()] == ["progress.json"]


def test_load_survives_corruption(tmp_path):
    p = tmp_path / "progress.json"
    p.write_text("{not json")
    assert state.load(p) == {}
    p.write_text("[1,2]")
    assert state.load(p) == {}


def test_no_plaintext_prose_saved(tmp_path):
    """进度文件不能存正文 —— 它躺在 ~/.local/share 里，谁都可能看到。"""
    p = tmp_path / "progress.json"
    st = state.update({}, FakeBook(), 900, 1, 40.0, "rg")
    state.save(st, p)
    raw = p.read_text()
    assert "x" * 50 not in raw
    for v in json.loads(raw)[FakeBook.key].values():
        assert not (isinstance(v, str) and len(v) > 20)


def test_panic_screen_looks_like_watch():
    s = panic.screen("$ ")
    assert "--watch" in s and "Found 0 errors" in s
    assert "Watching for file changes" in s


def test_panic_requires_ctrl_l(capsys):
    keys = iter(["n", "q", " ", "\x0c"])
    panic.enter(lambda: next(keys), "$ ")
    assert next(keys, "done") == "done"   # 前三个键都没能退出


def test_panic_ignores_esc_so_panicked_mashing_cannot_reopen_the_book():
    """**这条是老板键的核心保证。** 进入键是单击 Esc，紧张时人会连拍 —— 如果 Esc 在
    伪装屏里也响应，连拍就等于"进去又出来"，人还没走到你身后书就回到屏幕上了。

    read_key 的转义序列消歧同样重要（方向键首字节也是 Esc → None，这里一并钉住）。
    """
    keys = iter(["\x1b", "\x1b", "\x1b", None, "\x1b", "\x0c"])
    panic.enter(lambda: next(keys), "$ ")
    assert next(keys, "done") == "done"   # 四次 Esc + 一个转义序列都没能恢复


def test_resume_key_shares_no_byte_with_esc():
    """恢复键绝不能是含 Esc 的组合 —— 那会让上一条测试的保证失效。"""
    assert panic.RESUME != term.ESC
    assert not panic.RESUME.startswith(term.ESC)
    assert len(panic.RESUME) == 1 and ord(panic.RESUME) < 0x20


def test_workspace_pick_deterministic():
    a = workspace.pick(42)
    b = workspace.pick(42)
    assert a == b
    assert workspace.pick(43) != a or len(workspace.scan()[0]) == 1


def test_workspace_symbols_are_plausible():
    _, syms = workspace.scan()
    assert syms
    assert all(not s.startswith("_") for s in syms)
    assert all(s.lower() not in workspace.RESERVED for s in syms)


def test_workspace_symbols_are_short_enough_for_one_line():
    """符号名嵌在单行伪装里（`TODO(name): ...`）。本仓库自己就有 39 字符的测试函数名，
    不设上限时 rg 主题那行会在 72 列超宽 —— 这条钉住那次回归。"""
    _, syms = workspace.scan()
    assert all(len(s) <= workspace.MAX_SYM for s in syms)


def test_git_author_non_empty():
    name, mail = workspace.git_author()
    assert name and "@" in mail
