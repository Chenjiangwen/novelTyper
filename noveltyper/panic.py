"""老板键：单击 Esc 进入，Ctrl-L 恢复，**绝不自动恢复**。

三条硬约束：

1. **进入要一步到位** —— 单击 Esc。这个键是在紧张状态下按的，多一次按键就是多一次
   失败机会。代价是误触变多（想放弃输入、vim 肌肉记忆都会按 Esc），但误触只是屏幕跳到
   构建输出、丢个视觉焦点，一个 Ctrl-L 就回来了，不丢数据也不丢进度。
   这个方向依赖 `term.read_key` 的转义序列消歧 —— 方向键首字节也是 Esc，消歧一旦失效
   就变成"按方向键翻到老板键屏"。见 `term.py` 的模块 docstring。
2. **恢复键必须与 Esc 无关** —— 这条不能让。老板键失效的场合恰好是最需要它生效的场合：
   紧张时人会连拍。如果恢复也绑在 Esc 上（比如双击 Esc 恢复），三次 200ms 内的 Esc 就
   等于"进去又出来"，人还没走到你身后书就回到屏幕上了。
   Ctrl-L 除了不在乱按路径上，伪装语义也是顺的 —— 在一个真的 watch 进程前面按 Ctrl-L
   清屏刷新是完全自然的动作。
3. **绝不自动恢复** —— 照搬 fish-reader `autoExit: false` 的理由：必须手动关闭才安全。
   任何"N 秒后自动回到正文"的设计都可能在人还没走开时把书翻回来。

伪装选 `tsc --watch`：watch 语义天然解释了「为什么这个终端停在这里不动」。
"""
import sys
import time

from .themes import DIM, OFF

RESUME = "\x0c"          # Ctrl-L。见上面第 2 条：绝不能改成任何含 Esc 的组合


def screen(ps1):
    t = time.strftime("%H:%M:%S")
    return "\n".join([
        f"{ps1}npx tsc --watch --noEmit -p tsconfig.json",
        f"[{t}] Starting compilation in watch mode...",
        "",
        f"[{t}] Found 0 errors. Watching for file changes.",
    ])


def enter(read_key, ps1):
    """铺伪装屏并阻塞，直到读到 Ctrl-L。EOF/Ctrl-C 也退出，避免卡死在这一屏。

    Esc 在这里是**故意不响应**的：进入键就是 Esc，紧张时的连拍不能把书翻回来。
    """
    print(screen(ps1))
    sys.stdout.flush()
    while True:
        try:
            k = read_key()
        except (EOFError, KeyboardInterrupt):
            return
        if k == RESUME:
            print(f"{DIM}[{time.strftime('%H:%M:%S')}] File change detected. "
                  f"Starting incremental compilation...{OFF}")
            return
