"""打字模式：严格前进。伪装成往 golden fixture 写内容的 heredoc。

**这是本项目最早发现的 bug 所在，三条规则不能破：**

1. **严格前进** —— 只有敲对了游标才右移。老实现 `while len(buf) < len(target)` 只数长度
   不看对错，pty 实测连按 400 次空格就能"打"完整段并翻页，练打字的意义归零。
2. **错字显示目标字符** —— 敲错了渲染的仍是原文该出现的字符（标红提示），不是用户敲进去
   的那个。否则屏幕上的正文会被自己的手误改写，既读不下去也无从对照。
3. **ghost 宽度恒定** —— 每帧都渲染完整的 `target`，只切前后两段的颜色。老实现从左侧裁
   `rest`，行宽随输入变化，光标和折行都会漂。

**敲不出来的字符必须跳过，判据是结构性的而不是白名单。** `term.read_key` 一次 `os.read`
只取一个字节，所以它永远只能返回 ASCII —— 任何 `ord > 126` 的字符都不可能被匹配上，停在
它上面就是死锁。原实现用一张手写的 `SAFE` 字符表，漏掉的字符（实测 Ball Lightning 有
7438 个软连字符、Death's End 有 38 个汉字）就成了路障：屏幕上看不出异常，敲什么都不前进。
现在按 `ord > 126` 一律跳过 —— 白名单会漏，值域判据不会。
"""
import sys
import time

from .themes import DIM, GRN, RED, OFF

ABORT = ("\x03", "\x1b")          # Ctrl-C / Esc 放弃本段
BACKSPACE = ("\x7f", "\b")
REPLAY = "\x12"                   # Ctrl-R 重听当前行（听打时最需要的一个键）


def typeable(ch):
    """能否由一次单字节 read 产出、且不会被下面的控制键过滤挡掉。

    上界 126：`read_key` 一次 `os.read` 只取一个字节，非 ASCII 永远匹配不上。
    下界 32：主循环把 `ord < 32` 当控制键忽略（Enter/Tab 不该算击键），所以正文里真出现
    控制字符时同样是死锁。两头都卡住才是完整判据 —— 见模块 docstring。
    """
    return 32 <= ord(ch) <= 126


def _skip(target, pos):
    """从 pos 起跳过所有敲不出来的字符，返回新的 pos。"""
    while pos < len(target) and not typeable(target[pos]):
        pos += 1
    return pos


def _frame(target, pos, bad):
    """一帧：已敲对的绿、当前字符（错了标红）、剩余灰。宽度恒为 len(target)。"""
    done = f"{GRN}{target[:pos]}{OFF}" if pos else ""
    if pos >= len(target):
        return done
    cur = target[pos]
    head = f"{RED}\033[4m{cur}{OFF}" if bad else f"\033[4m{cur}\033[24m"
    rest = f"{DIM}{target[pos + 1:]}{OFF}" if pos + 1 < len(target) else ""
    return done + head + rest


def run(read_key, lines, target_path, ps1, say=None):
    """打完 lines 里的每一行。read_key 由调用方注入（term.read_key），便于测试。

    `say(text)` 可选，给一行就朗读一行 —— 听打练习。**按行而不是按整段朗读**：整段读完
    要十几秒，人早打到第三行了，声音和光标对不上就成了干扰而不是提示。Ctrl-R 重听当前行。

    返回 (wpm, accuracy) 或 None（中途放弃）。计时从第一次有效击键开始 —— 否则「看完
    这段再动手」的思考时间会算进速度里。
    """
    print(f"{ps1}cat > {target_path} <<'EOF'")
    hits = miss = chars = 0
    t0 = None
    for target in lines:
        pos, bad = _skip(target, 0), False
        if say:
            say(target)
        while pos < len(target):
            sys.stdout.write(f"\r\033[Kheredoc> {_frame(target, pos, bad)}")
            back = len(target) - pos
            if back:
                sys.stdout.write(f"\033[{back}D")
            sys.stdout.flush()
            k = read_key()
            if k is None:                      # 方向键等转义序列：忽略，不算击键
                continue
            if k in ABORT:
                sys.stdout.write("\r\033[K")
                return None
            if k == REPLAY:                    # 重听：不算击键，不动游标
                if say:
                    say(target)
                continue
            if k in BACKSPACE:                 # 退格只用来回看，不改计数
                pos, bad = max(pos - 1, 0), False
                continue
            if len(k) != 1 or ord(k) < 32:     # Enter/Tab 等控制键忽略
                continue
            t0 = t0 or time.monotonic()
            if k == target[pos]:
                hits += 1
                chars += 1
                pos, bad = _skip(target, pos + 1), False
            else:
                miss += 1
                bad = True                     # 不前进 —— 严格前进的全部含义
        sys.stdout.write(f"\r\033[Kheredoc> {GRN}{target}{OFF}\n")
    print("EOF")
    T = max(time.monotonic() - (t0 or time.monotonic()), 0.5)
    wpm = 60 * chars / T / 5                   # 净速度：只有敲对的字符算产出
    acc = 100 * hits / max(hits + miss, 1)
    print(f"{DIM}{chars} bytes written in {T:.0f}s "
          f"({wpm:.0f} wpm, {acc:.1f}% accuracy, {hits + miss} keystrokes){OFF}")
    return wpm, acc
