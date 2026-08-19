"""进度持久化 + 从 v1 段号格式迁移。

**不存明文正文** —— 进度文件躺在 `~/.local/share/` 里，谁都可能看到；只存偏移、计数、
主题 key，看上去就是个普通工具的状态文件。

写入用「临时文件 + os.replace」：原地写到一半被 Ctrl-C 打断会留下截断的 JSON，下次启动
直接崩，进度全丢。replace 在同一文件系统上是原子的。
"""
import contextlib
import json
import os
import tempfile
from pathlib import Path

PATH = Path.home() / ".local/share/noveltyper/progress.json"
VERSION = 2


def load(path=None):
    """整个状态文件 → dict。读不出来就当空的，绝不因为进度文件坏了而无法启动。"""
    p = Path(path or PATH)
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(st, path=None):
    """原子写。目录不存在就建。"""
    p = Path(path or PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".progress-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, p)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def record(st, book):
    """取某本书的记录，顺带把 v1 的 `seg` 段号迁到字符偏移。

    v1 的 `seg` 是**原始段落序号**（合并前、且没滤掉章节标题），所以要走 `raw_starts`
    才能换算 —— 直接当偏移用会把进度扔到书的最前面几页。
    """
    rec = dict(st.get(book.key) or {})
    if "offset" not in rec and "seg" in rec:
        seg = rec["seg"]
        if isinstance(seg, int) and 0 <= seg < len(book.raw_starts):
            rec["offset"] = book.raw_starts[seg]
            rec["migrated_from_seg"] = seg     # 留个痕，万一换算错了能看出来
    rec.setdefault("offset", 0)
    rec.setdefault("typed", 0)
    rec.setdefault("wpm", 0.0)
    rec["offset"] = max(0, min(int(rec["offset"]), max(len(book.text) - 1, 0)))
    return rec


def update(st, book, offset, typed, wpm, theme):
    st["_v"] = VERSION
    rec = dict(st.get(book.key) or {})
    rec.update(offset=int(offset), typed=int(typed), wpm=round(float(wpm), 1),
               theme=theme)
    rec.pop("seg", None)                       # 迁移完成，别留两个真相来源
    st[book.key] = rec
    return st
