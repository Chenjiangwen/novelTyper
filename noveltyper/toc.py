"""章节目录 + 跳转输入。每个主题用自己那套清单形式（见 `Theme.toc_row`）。

数字输入必须自己实现：cbreak 下没有行编辑，`input()` 会把 tty 状态搅乱。输入行本身也
要伪装 —— 一个突然出现的 `跳到第几章:` 提示比正文更容易露。
"""
import sys

from .themes import DIM, OFF


def show(theme, book, cur_off, cols, ps1):
    """打印目录。命令行也用主题自己的清单命令，跳转界面才不会自成一格。"""
    here = book.chapter_at(cur_off)
    print(f"{ps1}{theme.toc_cmd}")
    for n, (off, label) in enumerate(book.chapters):
        print(theme.toc_row(n, label, off, cols, bool(here) and n == here[0]))
    print(f"{DIM}{len(book.chapters)} entries{OFF}")


def ask(read_key, ps1, n):
    """读一个 0..n-1 的序号。回车确认，Esc/Ctrl-C 取消，返回 None 表示不跳。"""
    buf = ""
    while True:
        sys.stdout.write(f"\r\033[K{ps1}# jump to entry: {buf}")
        sys.stdout.flush()
        try:
            k = read_key()
        except (EOFError, KeyboardInterrupt):
            k = "\x03"
        if k is None:
            continue
        if k in ("\r", "\n"):
            sys.stdout.write("\r\033[K")
            return int(buf) if buf.isdigit() and int(buf) < n else None
        if k in ("\x7f", "\b"):
            buf = buf[:-1]
        elif k in ("\x03", "\x1b"):
            sys.stdout.write("\r\033[K")
            return None
        elif k.isdigit() and len(buf) < 3:
            buf += k
