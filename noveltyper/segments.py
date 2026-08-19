"""把原始段落合并成"一次按键推进一段"的阅读单元。

实测这本书 2822 段里 24.9% 短于 80 字符，最长有 25 段对白连击 —— 不合并的话在对白密集
处一次按键只出 20 个字符（`"You think I would?"`），一整屏 pytest 框架包一行对白，
既难读也不像真的报错。

算法沿用 fish-reader 的四条规则，阈值按英文语料调过（它的 120/600 是中文口径，中文
一个字≈一个词）：以整段为最小粒度累加，累计 ≥ TARGET 就停；前瞻式硬顶，再加一段会超
HARD_CAP 就不加；绝不跨章节标题合并；合并后保留段间空行。
"""
import bisect

TARGET = 200        # 累计到此长度就收；对应实测 median=176
HARD_CAP = 700      # 前瞻硬顶，保证单元长度 <= 此值（除非单段本身就超）
TRUNC = 320         # 单段超顶时的截断目标
WINDOW = 70         # 截断点在 [TRUNC-WINDOW, TRUNC+WINDOW] 内找句末标点
ENDINGS = '.!?"’”'


def _sentence_cut(text, around):
    """在 around 附近找句末标点，返回切点（下标，含标点）。

    反向扫是为了尽量多截 —— 取窗口内最靠后的标点，而不是最靠前的。英文标点后通常跟
    空格，所以顺带把引号收尾（`said."` / `it!"`）算作合法切点。
    """
    lo = max(around - WINDOW, 1)
    hi = min(around + WINDOW, len(text))
    for i in range(hi - 1, lo - 1, -1):
        if text[i] in ENDINGS and (i + 1 >= len(text) or text[i + 1] == " "):
            return i + 1
    return min(around, len(text))


def build(blocks, chapter_offsets, text):
    """[(start, end)] 原始段落 → [(start, end)] 阅读单元。

    chapter_offsets 是章节起始偏移的集合：单元不跨越它们，否则章节目录跳转会落到
    某个单元的中间，`chapter_at` 也会指错。
    """
    out = []
    i, n = 0, len(blocks)
    while i < n:
        start, end = blocks[i]
        i += 1
        while i < n:
            if blocks[i][0] in chapter_offsets:
                break                            # 不跨章节
            if end - start >= TARGET:
                break
            if blocks[i][1] - start > HARD_CAP:  # 前瞻：再加一段就超顶
                break
            end = blocks[i][1]
            i += 1
        if end - start > HARD_CAP:               # 单段本身超顶，切开
            out.extend(_split(text, start, end))
        else:
            out.append((start, end))
    return out


def _split(text, start, end):
    """把一个超长段切成若干 <= HARD_CAP 的片，切点优先落在句末。"""
    while end - start > HARD_CAP:
        cut = start + _sentence_cut(text[start:end], TRUNC)
        if cut <= start or cut >= end:           # 找不到标点，硬切兜底
            cut = start + TRUNC
        yield start, cut
        start = cut
        while start < end and text[start] == " ":
            start += 1
    yield start, end


def index_at(units, off):
    """偏移 → 单元序号。恢复进度时偏移可能落在单元中间，取包含它的那个。"""
    i = bisect.bisect_right([u[0] for u in units], off) - 1
    return max(0, min(i, len(units) - 1))
