"""终端底层：按键读取、Esc 消歧、cbreak、真实 PS1 复用。

**Esc 消歧是这个模块存在的理由，而且它现在是关键路径。** 方向键发的是 `\\x1b[A` ——
三个字节，首字节和裸 Esc 完全相同。老板键是**单击** Esc 触发的（见 `panic.py`），所以
消歧一旦失效，按方向键就会直接翻到构建输出屏。
判据是时间：转义序列的后续字节紧随其后（同一个 read 就绪），人手指按不出 30ms 内的
两次按键。所以 Esc 后 select 等 ESC_TIMEOUT，有字节就整串读掉丢弃，没有才是裸 Esc。

用 os.read 而不是 sys.stdin.read —— 后者带缓冲，select 会说"没数据"而实际数据在
Python 的缓冲区里。
"""
import contextlib
import os
import re
import select
import subprocess
import sys
import termios
import tty
from pathlib import Path

ESC_TIMEOUT = 0.03      # 裸 Esc 与转义序列的分界；人手按不出这么快的连击
ESC = "\x1b"

CSI_FINAL = re.compile(r"[\x40-\x7e]")   # CSI 序列的终止字节范围


def read_key(fd=0):
    """读一次按键。返回单字符；方向键等转义序列整串丢弃后返回 ESC_SEQ 哨兵。

    返回值只有三类：普通字符、ESC（裸 Esc，可用于老板键）、None（转义序列，调用方忽略）。
    """
    b = os.read(fd, 1)
    if not b:
        raise EOFError
    if b != b"\x1b":
        return b.decode("utf-8", "replace")
    # Esc 之后：有后续字节 → 转义序列；超时 → 裸 Esc
    if not select.select([fd], [], [], ESC_TIMEOUT)[0]:
        return ESC
    nxt = os.read(fd, 1).decode("latin-1")
    if nxt == "[" or nxt == "O":
        # CSI / SS3：读到终止字节为止，整串丢弃
        for _ in range(16):
            if not select.select([fd], [], [], ESC_TIMEOUT)[0]:
                break
            if CSI_FINAL.fullmatch(os.read(fd, 1).decode("latin-1")):
                break
    return None


@contextlib.contextmanager
def cbreak(fd=0):
    """cbreak：逐键读取但保留 Ctrl-C。finally 里恢复，异常路径也不会留坏终端。"""
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def real_prompt():
    """复用机器真实的 shell 提示符 —— 自己编的提示符和会话里其它行不一致，一眼假。"""
    for args in (["zsh", "-i", "-c", 'print -rnP -- "$PS1"'],
                 ["bash", "-i", "-c", 'echo -n "${PS1@P}"']):
        try:
            p = subprocess.run(args, capture_output=True, timeout=4, text=True)
            s = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", p.stdout)  # 去 OSC 标题
            if s.strip():
                return s.rstrip("\n")
        except (OSError, subprocess.SubprocessError):
            pass
    user = os.environ.get("USER", "user")
    return f"{user}@{os.uname().nodename.split('.')[0]} {Path.cwd().name} % "


def cols(lo=48, hi=100):
    """正文宽度上限 100 —— 再宽一行字太多，眼睛要横扫，读长文反而累。"""
    try:
        return max(lo, min(os.get_terminal_size().columns, hi))
    except OSError:
        return 80


def set_title(s):
    """终端标题也是穿帮面 —— 标签页上写着书名就白干了。"""
    sys.stdout.write(f"\033]0;{s}\007")


def clear_line():
    sys.stdout.write("\r\033[K")
