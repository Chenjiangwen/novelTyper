"""term.read_key 的 Esc 消歧 —— 用真 pty，因为判据是时间与 select 的行为。

这条路径没法用 mock 测：`select` 对普通管道和 pty 的就绪语义不同，而 bug 恰恰出在
「以为没数据、其实在 Python 缓冲区里」。

子进程里必须先进 cbreak（`term.cbreak`）—— 默认的 canonical 模式下 `os.read(0, 1)` 要等
到换行才返回，测试会挂死。顺手关掉 ECHO，注入的按键才不会混进输出里。
"""
import contextlib
import os
import pty
import select
import signal
import sys
import time
from pathlib import Path

from noveltyper import term

ROOT = str(Path(term.__file__).resolve().parents[1])

DRIVER = r"""
import sys, termios
sys.path.insert(0, {root!r})
from noveltyper import term
with term.cbreak(0):
    a = termios.tcgetattr(0)
    a[3] &= ~termios.ECHO                      # 关回显，输出里只剩判定结果
    termios.tcsetattr(0, termios.TCSADRAIN, a)
    out = []
    for _ in range({n}):
        try:
            k = term.read_key(0)
        except (EOFError, OSError):
            break
        out.append("SEQ" if k is None else ("ESC" if k == term.ESC else repr(k)))
    sys.stdout.write("RESULT " + "|".join(out) + " END\n")
    sys.stdout.flush()
"""


def drive(keys, n, deadline=8.0):
    """在 pty 子进程里注入按键，返回 read_key 的判定列表。

    keys 是 [(bytes, 注入后 sleep 秒数)]。间隔必须 > ESC_TIMEOUT，否则裸 Esc 会被
    后一个按键的首字节吞成转义序列 —— 那正是这套判据要区分的两种情况。

    判定之间用 `|` 分隔而不是空格：空格键的 repr 是 `"' '"`，自带空格。
    """
    pid, fd = pty.fork()
    if pid == 0:                                   # 子进程
        os.execv(sys.executable, [sys.executable, "-c",
                                  DRIVER.format(root=ROOT, n=n)])
    buf = b""
    try:
        time.sleep(0.3)                            # 等子进程进 cbreak
        for chunk, delay in keys:
            os.write(fd, chunk)
            time.sleep(delay)
        end = time.monotonic() + deadline
        while b"END" not in buf and time.monotonic() < end:
            if not select.select([fd], [], [], 0.2)[0]:
                continue
            try:
                b = os.read(fd, 4096)
            except OSError:
                break
            if not b:
                break
            buf += b
    finally:
        with contextlib.suppress(OSError, ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        os.close(fd)
        os.waitpid(pid, 0)
    text = buf.decode(errors="replace")
    assert "RESULT" in text, f"driver produced no result: {text!r}"
    return text.split("RESULT", 1)[1].split("END", 1)[0].strip().split("|")


def test_arrow_keys_are_not_bare_esc():
    """方向键必须判成 SEQ —— 老实现在这里误进老板键。"""
    out = drive([(b"\x1b[A", 0.15), (b"\x1b[B", 0.15), (b"\x1bOP", 0.15)], 3)
    assert out == ["SEQ", "SEQ", "SEQ"]


def test_bare_esc_detected():
    out = drive([(b"\x1b", 0.25), (b"\x1b", 0.25)], 2)
    assert out == ["ESC", "ESC"]


def test_mixed_sequence():
    keys = [(b"\x1b[A", 0.15), (b"\x1b", 0.25), (b"n", 0.15), (b" ", 0.15)]
    out = drive(keys, 4)
    assert out == ["SEQ", "ESC", "'n'", "' '"]


def test_enter_arrives_as_newline_in_cbreak():
    """cbreak 不关 ICRNL，所以回车到达时已是 `\\n` —— app.py 的 NEXT 同时收 \\r 和 \\n
    正是为此；只认 `\\r` 会让回车翻不了页。"""
    out = drive([(b"q", 0.15), (b"\x07", 0.15), (b"\r", 0.15)], 3)
    assert out == ["'q'", "'\\x07'", "'\\n'"]


def test_cols_bounds():
    n = term.cols(48, 100)
    assert 48 <= n <= 100


def test_clear_line_and_title_are_pure_escapes(capsys):
    term.set_title("tsc --watch")
    term.clear_line()
    out = capsys.readouterr().out
    assert out == "\033]0;tsc --watch\007\r\033[K"
