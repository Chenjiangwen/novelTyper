"""打字模式：严格前进。伪装成往 golden fixture 写内容的 heredoc。

**这是本项目最早发现的 bug 所在，三条规则不能破：**

1. **严格前进** —— 只有敲对了游标才右移。老实现 `while len(buf) < len(target)` 只数长度
   不看对错，pty 实测连按 400 次空格就能"打"完整段并翻页，练打字的意义归零。
2. **错字显示目标字符** —— 敲错了渲染的仍是原文该出现的字符（标红提示），不是用户敲进去
   的那个。否则屏幕上的正文会被自己的手误改写，既读不下去也无从对照。
3. **ghost 宽度恒定** —— 每帧都渲染完整的 `target`，只切前后两段的颜色。老实现从左侧裁
   `rest`，行宽随输入变化，光标和折行都会漂。

无法输入的字符（`SAFE`：变音符号、版权号等）到了就自动跳过 —— 键盘敲不出来的东西不该
成为路障，也不该记成错误。
"""
import sys
import time

from .themes import DIM, GRN, RED, OFF

# 键盘（美式布局）敲不出来的字符：到了就自动跳过，不计击键也不计错。
SAFE = set("ÜàéíïöüÄäÖö©®±°×÷£€¥§¶†‡")
ABORT = ("\x03", "\x1b")          # Ctrl-C / Esc 放弃本段
BACKSPACE = ("\x7f", "\b")


def _skip(target, pos):
    """从 pos 起跳过所有敲不出来的字符，返回新的 pos。"""
    while pos < len(target) and target[pos] in SAFE:
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


def run(read_key, lines, target_path, ps1):
    """打完 lines 里的每一行。read_key 由调用方注入（term.read_key），便于测试。

    返回 (wpm, accuracy) 或 None（中途放弃）。计时从第一次有效击键开始 —— 否则「看完
    这段再动手」的思考时间会算进速度里。
    """
    print(f"{ps1}cat > {target_path} <<'EOF'")
    hits = miss = chars = 0
    t0 = None
    for target in lines:
        pos, bad = _skip(target, 0), False
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
