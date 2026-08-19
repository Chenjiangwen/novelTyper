"""主循环。pull 模型：每次按键推进一个单元，输出内联进当前会话。

按键设计的两条纪律：
  - **推进键要多**（Enter/n/空格/j）—— 读得顺的时候手不该去找特定键。
  - **危险键要少且不与推进键相邻** —— `q` 退出、Esc 老板键、Ctrl-L 恢复。

未识别的可打印键回一句 `zsh: command not found:` —— 误按也留在伪装里，不会出现
"未知按键"这类只有阅读器才会说的话。
"""
import sys
import time
from pathlib import Path

from . import book as bookmod
from . import panic, segments, state, term, themes, toc, typemode
from .themes import DIM, OFF

NEXT = ("\r", "\n", "n", " ", "j")
PREV = ("p", "k")
QUIT = ("q", "\x04")


def books():
    """所有能读的 epub，按目录优先级 + 文件名排序。"""
    out = []
    for d in (Path("novel_data"), Path.home() / ".local/share/noveltyper/books"):
        if d.is_dir():
            out += sorted(d.glob("*.epub"))
    return out


def find_book(argv):
    """定位 epub。参数可以是路径，也可以是书名的一部分（大小写不敏感）。

    书架上放几本是常态，但**命令行参数是穿帮面** —— `.zsh_history` 里留一行
    `corpus-verify "The Dark Forest (Cixin Liu).epub"` 就白干了。所以支持子串匹配：
    `corpus-verify dark` 既短又不像书名。匹配到多本时列出候选让人再缩一次，不猜。
    """
    pool = books()
    if not argv:
        return str(pool[0]) if pool else None
    if Path(argv[0]).exists():
        return argv[0]
    q = argv[0].lower()
    hits = [b for b in pool if q in b.name.lower()]
    if len(hits) == 1:
        return str(hits[0])
    if hits:
        sys.exit("ambiguous: " + ", ".join(b.stem[:34] for b in hits))
    return None


def load_book(path):
    """解析 epub。坏文件/非 epub 要给一句人话，不能甩 zipfile 的栈 —— 第一次运行
    拿错文件的概率最高，而一堆 traceback 会让人以为程序本身是坏的。"""
    try:
        return bookmod.load(path)
    except Exception as e:                       # noqa: BLE001 - 解析失败的形态太多
        sys.exit(f"cannot read {Path(path).name}: {type(e).__name__}: {e}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    path = find_book(argv)
    if not path:
        sys.exit("usage: corpus-verify [book.epub]   (or drop one in ./novel_data/)")

    bk = load_book(path)
    units = segments.build(bk.blocks, {o for o, _ in bk.chapters}, bk.text)
    if not units:
        sys.exit("no readable text in that file")

    st = state.load()
    rec = state.record(st, bk)
    i = segments.index_at(units, rec["offset"])
    typed, best = rec["typed"], rec["wpm"]
    theme = themes.get(rec.get("theme", ""))

    ps1 = term.real_prompt()
    # 标题也是穿帮面：写成一条正在跑的命令，标签页上就看不出在读小说。
    term.set_title("tsc --watch")
    started = time.monotonic()
    fd = sys.stdin.fileno()

    def ctx(cols):
        start, end = units[i]
        ch = bk.chapter_at(start)
        return themes.Ctx(i=i, total=len(units), text=bk.text[start:end],
                          chapter=ch[2] if ch else "", cols=cols, ps1=ps1,
                          offset=start, seed=start)

    try:
        with term.cbreak(fd):
            pending = True
            while True:
                cols = term.cols()
                if pending:
                    print(themes.render(theme, ctx(cols)))
                    pending = False
                sys.stdout.write(ps1)
                sys.stdout.flush()
                k = term.read_key(fd)
                term.clear_line()
                if k is None:                 # 方向键等：吞掉，绝不误进老板键
                    continue
                if k in QUIT:
                    break
                if k in NEXT:
                    i, pending = min(i + 1, len(units) - 1), True
                elif k in PREV:
                    i, pending = max(i - 1, 0), True
                elif k == "s":
                    theme = themes.get(themes.next_key(theme.key))
                    pending = True            # 换主题要重新折行，必须整段重渲染
                elif k == "c":
                    toc.show(theme, bk, units[i][0], cols, ps1)
                    n = toc.ask(term.read_key, ps1, len(bk.chapters))
                    if n is not None:
                        i, pending = segments.index_at(units, bk.chapters[n][0]), True
                    print()
                elif k == "t":
                    c = ctx(cols)
                    lines = themes.wrap(c.text, cols - 10)
                    r = typemode.run(lambda: term.read_key(fd), lines,
                                     theme.typing_target(c), ps1)
                    if r:
                        typed += 1
                        best = max(best, r[0])
                    print()
                elif k == term.ESC:
                    # 单击就进 —— 紧张时多按一次就是多一次失败机会。误触的代价只是屏幕
                    # 跳到构建输出，Ctrl-L 就回来了。方向键在上面已经被吞掉了。
                    panic.enter(lambda: term.read_key(fd), ps1)
                    print()
                elif k.isprintable():
                    print(f"zsh: command not found: {k}")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        off = units[i][0]
        state.save(state.update(st, bk, off, typed, best, theme.key))
        ch = bk.chapter_at(off)
        print(f"{DIM}{i + 1}/{len(units)} ({(i + 1) / len(units) * 100:.1f}%)"
              + (f" · {ch[2][:36]}" if ch else "")
              + f" · {(time.monotonic() - started) / 60:.0f} min"
              + (f" · typed {typed}, best {best:.0f} wpm" if typed else "") + OFF)


if __name__ == "__main__":
    main()
