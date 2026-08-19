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
import socket
import struct
import subprocess
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


def run_app(book, keys, home, settle=2.0, deadline=25.0, cols=100, cwd=None):
    """在 pty 里跑 app.main，注入按键，返回 (输出文本, waitpid status)。"""
    # **fork 之后到 execve 之间不做任何 Python 层的分配。** 全量跑时 pytest 进程里有别的
    # 线程（TTS stub server 的等待、朗读的 daemon 线程），fork 一个多线程进程时子进程可能
    # 拿到别的线程持着的 malloc 锁 → 卡在 fork 与 execve 之间，被 deadline 打死，表现成
    # 「进度文件没写出来」这种完全不像死锁的样子（全量跑时偶发过一次，单跑必过）。
    # 所以 env 与 argv 都在父进程里算好，子进程分支里只剩一句 execve。
    env = {**os.environ, "HOME": str(home), "TERM": "xterm-256color"}
    argv = [sys.executable, "-c", DRIVER.format(root=ROOT, book=str(book))]
    cwd = str(cwd) if cwd else None
    pid, fd = pty.fork()
    if pid == 0:
        if cwd:
            os.chdir(cwd)                  # 只有一次 syscall，不分配
        os.execve(sys.executable, argv, env)

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
        (b"\t", 0.3),                 # Tab 重听：没开朗读时是彻底的空操作，不能推进
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
    assert "command not found: \t" not in out     # Tab 有自己的分支，不掉进兜底
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


TTS_SERVER = r"""
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200); self.end_headers()
        self.wfile.write(b"RIFF" + b"\0" * 64)
    def log_message(self, *a): pass
s = HTTPServer(("127.0.0.1", int(sys.argv[1])), H)
s.serve_forever()
"""


@contextlib.contextmanager
def mute_tts(work):
    """在 `work/tts.json` 里配一个指向本地 server 的 http 后端。

    真跑 `say`/`edge` 会出声、会联网、会让这条测试慢一个数量级 —— 但**后端不能 stub 掉**：
    被测进程是真 `os.execve` 起的，monkeypatch 到不了。所以走 `http` 后端指到 127.0.0.1，
    顺带把「项目内 tts.json 选后端」这条路径也一起端到端验证了。返回的是假音频字节，
    `afplay` 立刻失败（stderr 已 DEVNULL）→ 不出声。

    **写进 `work/` 而不是仓库根。** `tts.config_path()` 优先找 `./tts.json`，被测进程的
    CWD 必须是隔离目录，否则会读到本仓库自己的那份配置（后端是 edge → 真联网出声）。

    **server 必须起在子进程而不是线程里。** `run_app` 用 `pty.fork()`，fork 一个多线程
    进程时子进程可能拿到别的线程持着的 malloc 锁 → 死锁（Python 3.13 为此发警告）。
    """
    with socket.socket() as probe:                 # 借一个空闲端口再让开
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    srv = subprocess.Popen([sys.executable, "-c", TTS_SERVER, str(port)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):                       # 等它真的开始监听
            with contextlib.suppress(OSError), socket.create_connection(
                    ("127.0.0.1", port), timeout=0.2):
                break
            time.sleep(0.05)
        (work / "tts.json").write_text(json.dumps({"backend": "http", "http": {
            "url": f"http://127.0.0.1:{port}/speak", "audio": "wav"}}))
        yield
    finally:
        srv.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            srv.wait(timeout=3)


def test_app_with_tts_enabled_survives_the_whole_walk(synth_epub, tmp_path):
    """**开着朗读走一遍主循环。** 单元测试覆盖了 Engine，但这些才是只有真跑才验证到的：

      - `v` 键的开关行文（`[tts] ...`）确实打出来，而且没有 Traceback；
      - **Tab 在阅读模式重听**：不重渲染这一段（屏幕上出现两份相同的"构建输出"比没声音
        更可疑），也不掉进 `command not found` 那条兜底分支；
      - 五处 `voice.stop()` 联动（目录、打字、老板键、关闭、finally）不会卡住主循环 ——
        `stop()` 里持锁 + terminate 子进程，写错了表现是退出时挂死而不是报错；
      - 退出时把 `tts`/`voice` 存进了 progress.json，下次启动才接得上；
      - 朗读线程是 daemon —— 不是的话进程退不出去，这条测试会超时被 SIGKILL。
    """
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    with mute_tts(work):
        keys = [
            (b"v", 0.6),                  # 开朗读
            (b"n", 0.6), (b"n", 0.6),     # 翻两段：抢占 + 预取都走到
            (b"\t", 0.6), (b"\t", 0.6),   # Tab 重听两次：不推进、不重渲染
            (b"c", 0.5), (b"\r", 0.4),    # 目录（stop 联动），回车不跳转
            (b"t", 0.6), (b"\x1b", 0.5),  # 打字模式（按行朗读）再放弃
            (b"\x1b", 0.5), (b"\x0c", 0.5),   # 老板键 → 必须静音 → Ctrl-L 回来
            (b"q", 0.8),
        ]
        out, status = run_app(synth_epub, keys, home, cwd=work)

    assert "Traceback" not in out, out[-3000:]
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, out[-2500:]
    assert "[tts] http" in out                    # 开关行、且选中的是项目内 tts.json 的后端
    assert "command not found" not in out         # Tab 不能掉进兜底分支
    assert "%)" in out and "min" in out           # finally 走完了，没在 stop 里挂死

    rec = json.loads((home / ".local/share/noveltyper/progress.json").read_text())
    key = next(k for k in rec if k != "_v")
    assert rec[key]["tts"] is True and rec[key]["voice"] == "http"
    # Tab 按了两次却没推进 —— 重听不是翻页。offset 只应反映上面那两次 `n`。
    assert rec[key]["offset"] > 0
