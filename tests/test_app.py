"""端到端：在真 pty 里启动 app.main，走一遍全部按键分支。

这是唯一会执行 `app.py` 整条 import 链和主循环的测试。单元测试各自覆盖了模块，但
主循环里的按键分发、pending 重渲染、finally 里的存档都只有真跑起来才验证得到。

两个必须这样写的地方（第一版都踩了）：
  1. **读取必须与注入并行**。渲染一屏几百字节，pty 输出缓冲填满后子进程阻塞在 write
     上，按键一个都不消费；等注入结束再读，所有按键会一次性到达，`read_key` 的 30ms
     Esc 判据全部失效。所以起一个读取线程。
  2. **不能先 sleep 再读**。macOS 上最后一个 slave fd 关闭时会丢弃未读数据，子进程
     早退（比如坏 epub）的输出就全没了。

HOME 指向 tmp_path —— `state.PATH` 是从 `Path.home()` 算的，不隔离就会把真进度覆盖掉。
"""
import contextlib
import fcntl
import json
import os
import pty
import signal
import struct
import sys
import termios
import threading
import time
from pathlib import Path

import pytest

from noveltyper import app, term

ROOT = str(Path(term.__file__).resolve().parents[1])

DRIVER = r"""
import sys, termios
sys.path.insert(0, {root!r})
a = termios.tcgetattr(0)
a[3] &= ~termios.ECHO                          # 关回显，注入的按键不混进输出
termios.tcsetattr(0, termios.TCSADRAIN, a)
from noveltyper.app import main
main([{book!r}])
"""


def run_app(book, keys, home, settle=2.0, deadline=25.0, cols=100):
    """在 pty 里跑 app.main，注入按键，返回 (输出文本, waitpid status)。"""
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["HOME"] = str(home)
        os.environ["TERM"] = "xterm-256color"
        os.execve(sys.executable,
                  [sys.executable, "-c", DRIVER.format(root=ROOT, book=str(book))],
                  os.environ)

    # pty.fork 默认 0x0，`term.cols` 会退到下限 48；设成真实宽度才测到常用路径。
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, cols, 0, 0))

    chunks, alive = [], True

    def reader():
        while True:
            try:
                b = os.read(fd, 65536)
            except OSError:                    # 子进程退出后 master 读到 EIO
                return
            if not b:
                return
            chunks.append(b)

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    status = None
    try:
        time.sleep(settle)                     # 等 epub 解析 + real_prompt 起 shell
        for chunk, delay in keys:
            os.write(fd, chunk)
            time.sleep(delay)
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            done, st = os.waitpid(pid, os.WNOHANG)
            if done:
                status = st
                alive = False
                break
            time.sleep(0.1)
    finally:
        if alive:
            with contextlib.suppress(OSError, ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(OSError):
                _, status = os.waitpid(pid, 0)
        t.join(timeout=2.0)
        os.close(fd)
        t.join(timeout=2.0)
    return b"".join(chunks).decode(errors="replace"), status


def shelf(tmp_path, monkeypatch, *names):
    """在隔离的 CWD/HOME 下摆一个书架，返回 novel_data 目录。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "novel_data"
    d.mkdir()
    for n in names:
        (d / n).write_bytes(b"PK\x03\x04")       # 只测定位，不解析
    return d


def test_find_book_matches_by_substring(tmp_path, monkeypatch):
    """**书名要能用子串指定** —— 命令行参数会留在 `.zsh_history` 里，是穿帮面。

    `corpus-verify dark` 看着像个构建目标；`corpus-verify "The Dark Forest (Cixin
    Liu).epub"` 一行就把伪装拆了。大小写不敏感，因为敲的时候不会去核对原文件名。
    """
    shelf(tmp_path, monkeypatch, "Ball Lightning.epub", "The Dark Forest.epub")
    assert app.find_book(["dark"]).endswith("The Dark Forest.epub")
    assert app.find_book(["DARK"]).endswith("The Dark Forest.epub")
    assert app.find_book(["ball"]).endswith("Ball Lightning.epub")


def test_find_book_ambiguous_exits_instead_of_guessing(tmp_path, monkeypatch):
    """匹配到多本时报候选并退出。**猜一本是最坏的行为** —— 打开的不是想读的那本，
    进度会记到另一本书名下，等发现时两边的偏移都已经脏了。"""
    shelf(tmp_path, monkeypatch, "Death's End.epub", "The Dark Forest.epub")
    with pytest.raises(SystemExit) as e:
        app.find_book(["e"])                     # 两本都含 e
    assert "ambiguous" in str(e.value)


def test_find_book_prefers_explicit_path_over_matching(tmp_path, monkeypatch):
    """存在的路径直接用，不走匹配 —— 书架外的文件也得能读。"""
    shelf(tmp_path, monkeypatch, "Ball Lightning.epub")
    other = tmp_path / "elsewhere.epub"
    other.write_bytes(b"PK\x03\x04")
    assert app.find_book([str(other)]) == str(other)
    assert app.find_book([]).endswith("Ball Lightning.epub")


def test_find_book_no_match_falls_through_to_usage(tmp_path, monkeypatch):
    """没匹配上要返回 None（调用方打 usage），不能静默开第一本。"""
    shelf(tmp_path, monkeypatch, "Ball Lightning.epub")
    assert app.find_book(["nonexistent"]) is None


def test_app_full_keyboard_walk(synth_epub, tmp_path):
    """一次会话里走遍：推进、后退、换主题、目录、打字、老板键、退出。

    `Traceback not in out` 这条比逐个功能断言更值钱 —— 主循环的 import 链和按键分发
    只有真跑起来才会被执行。
    """
    home = tmp_path / "home"
    home.mkdir()
    keys = [
        (b"n", 0.35),                 # 推进
        (b" ", 0.35),                 # 空格也推进
        (b"\r", 0.35),                # 回车（cbreak 下到达时是 \n）
        (b"p", 0.35),                 # 后退
        (b"s", 0.4),                  # 换主题
        (b"s", 0.4),
        (b"\x1b[A", 0.3),             # 方向键：必须被吞掉，不能进老板键
        (b"z", 0.3),                  # 未识别键 → command not found
        (b"c", 0.5), (b"1", 0.3), (b"\r", 0.5),        # 目录 → 跳第 1 章
        (b"t", 0.5), (b"\x1b", 0.5),  # 进打字模式再放弃
        (b"\x1b", 0.5),               # 单击 Esc → 老板键
        (b"\x1b", 0.3),               # 伪装屏里的 Esc 必须无效（连拍不能把书翻回来）
        (b"\x0c", 0.5),               # Ctrl-L 恢复
        (b"q", 0.6),                  # 退出
    ]
    out, status = run_app(synth_epub, keys, home)

    assert "Traceback" not in out, out[-3000:]
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, out[-2500:]
    assert "command not found: z" in out          # 误按也留在伪装里
    assert "--watch" in out and "Found 0 errors" in out   # 老板键那一屏出现过
    assert "jump to entry" in out                 # 目录跳转走到了
    assert "<<'EOF'" in out                       # 打字模式进过
    assert "%)" in out and "min" in out           # finally 的收尾统计行

    prog = home / ".local/share/noveltyper/progress.json"
    assert prog.is_file()                         # 进度落到隔离 HOME，不碰真文件
    rec = json.loads(prog.read_text())
    assert rec["_v"] >= 2
    key = next(k for k in rec if k != "_v")
    assert rec[key]["offset"] > 0 and "seg" not in rec[key]
    assert rec[key]["theme"] in [t.key for t in __import__(
        "noveltyper.themes", fromlist=["ALL"]).ALL]


def test_app_reports_bad_book_without_traceback(tmp_path):
    """拿错文件要给一句人话就退出 —— 首次运行踩到这条的概率最高。"""
    home = tmp_path / "home"
    home.mkdir()
    bad = tmp_path / "notabook.epub"
    bad.write_bytes(b"this is not a zip file")
    out, status = run_app(bad, [], home, settle=0.0, deadline=10.0)
    assert "Traceback" not in out, out[-2000:]
    assert "cannot read notabook.epub" in out
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) != 0


def test_app_resumes_from_saved_offset(synth_epub, tmp_path):
    """第二次启动必须从上次的偏移接着读 —— 存档没被读回来是最不容易发现的回归。"""
    home = tmp_path / "home"
    home.mkdir()
    out1, st1 = run_app(synth_epub, [(b"n", 0.35), (b"n", 0.35), (b"q", 0.6)], home)
    assert os.WEXITSTATUS(st1) == 0, out1[-2000:]
    prog = home / ".local/share/noveltyper/progress.json"
    first = json.loads(prog.read_text())
    key = next(k for k in first if k != "_v")
    off = first[key]["offset"]
    assert off > 0

    out2, st2 = run_app(synth_epub, [(b"q", 0.6)], home)
    assert os.WEXITSTATUS(st2) == 0, out2[-2000:]
    # 没动就退出 → 偏移不变；变了说明启动时没读存档（会回到书的开头）。
    assert json.loads(prog.read_text())[key]["offset"] == off
