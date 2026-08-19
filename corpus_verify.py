#!/usr/bin/env python3
"""兼容入口 —— 保留肌肉记忆里的 `python3 corpus_verify.py`。

真正的实现搬进了 `noveltyper/` 包。这个文件只做转发，不要在这里加逻辑。
文件名故意保持不变：`.zsh_history` 和 `ps aux` 里出现的仍是"验证语料"。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from noveltyper.app import main   # noqa: E402

if __name__ == "__main__":
    main()
