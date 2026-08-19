"""四种伪装主题。正文行前缀严格等宽，噪音行前缀自由变化。

这条取舍是整个视觉设计里最重要的：等宽前缀让正文左对齐、可以长时间阅读；噪音行（命令、
统计、分隔线）反而要长短不齐才像真的终端输出。

四种主题都用本机真命令抓过输出形状。cols=100 时正文可用宽度：
  git diff  99  （`+` 前缀仅 1 列，最宽 —— 且绿加行/灰上下文天然突出正文）
  go test   92  （8 空格缩进）
  ripgrep   95  （`NNNNN:` 定宽行号）
  pytest    90  （`E` + 9 空格，最窄）
TAP（node --test）已排除：长文本被塞进单引号挤成一行，没有稳定前缀。
"""
import random
import textwrap
import time
from dataclasses import dataclass

from . import workspace

DIM, RED, GRN, CYA, YEL, OFF = ("\033[90m", "\033[31m", "\033[32m",
                                "\033[36m", "\033[33m", "\033[0m")


@dataclass
class Ctx:
    """渲染一段所需的全部信息。主题不碰 Book，只读这些字段。"""
    i: int              # 单元序号，从 0
    total: int          # 单元总数
    text: str           # 正文
    chapter: str        # 当前章节标签，可能为 ""
    cols: int
    ps1: str
    offset: int         # 字符偏移，用于派生"看起来像真的"的数字
    seed: int           # = offset，给 workspace.pick 保证同段选同文件

    @property
    def elapsed(self):
        """耗时由序号线性导出 —— 数字纪律：不能倒退，也不能与进度脱节。"""
        return self.i * 0.0021 + 0.44


def wrap(text, width):
    return textwrap.wrap(text, max(20, width)) or [""]


class Theme:
    key = label = prefix = ""
    toc_cmd = ""

    def command(self, c):
        raise NotImplementedError

    def render(self, c):
        raise NotImplementedError

    def typing_target(self, c):
        """打字模式伪装成写文件 —— 每个主题写自己那套体系里合理的路径。"""
        return f"corpus/seg-{c.i:05d}.txt"

    def toc_row(self, n, label, off, cols, here):
        """目录一行。默认长得像测试用例清单，四个主题都说得通。"""
        mark = f"{CYA}→{OFF}" if here else " "
        return (f"{mark} {DIM}[{n:02d}]{OFF} {label[:max(10, cols - 26)]} "
                f"{DIM}@{off:06d}{OFF}")

    def body(self, c):
        """正文行，已加等宽前缀。子类通常直接用。"""
        return [f"{DIM}{self.prefix}{OFF}{ln}"
                for ln in wrap(c.text, c.cols - len(self.prefix))]


class Pytest(Theme):
    """一段 → 一次 golden fixture 比对失败。断言正文里出现整段自然语言完全合理。

    `--sw --sw-skip` 是真实存在的标志对（stepwise + stepwise-skip）：跳过上次那个失败、
    跑到下一个失败就停。所以「敲一次命令前进一段」本身就是这条命令的正常语义。
    """
    key, label, prefix = "pytest", "pytest golden fixture", "E         "
    toc_cmd = "pytest tests/corpus/test_golden.py --collect-only -q"

    def command(self, c):
        return "pytest tests/corpus/test_golden.py --sw --sw-skip -q"

    def toc_row(self, n, label, off, cols, here):
        mark = f"{CYA}→{OFF}" if here else " "
        return (f"{mark} {DIM}<Function test_golden_segment[{n:02d}]>{OFF} "
                f"{label[:max(10, cols - 52)]} {DIM}@{off:06d}{OFF}")

    def typing_target(self, c):
        return f"tests/corpus/golden/seg-{c.i:05d}.txt"

    def render(self, c):
        f, fn, _ = workspace.pick(c.seed)
        node = f"tests/corpus/test_golden.py::test_golden_segment[seg-{c.i:05d}]"
        bar = "=" * max(10, (c.cols - 11) // 2)
        pad = max(4, (c.cols - len(node) - 2) // 2)
        return [
            f"{DIM}stepwise: skipped seg-{max(c.i - 1, 0):05d}, resuming{OFF}",
            "",
            f"{DIM}{bar} FAILURES {bar}{OFF}",
            f"{DIM}{'_' * pad} {node} {'_' * pad}{OFF}",
            "",
            f"{DIM}    def test_golden_segment(seg):{OFF}",
            f"{DIM}        actual = {fn}(load_shard(seg)){OFF}",
            f"{DIM}>       assert actual == read_golden(seg){OFF}",
            f"{DIM}E       AssertionError: shard differs from golden fixture{OFF}",
            f"{DIM}E       assert ({len(c.text)} chars) == (0 chars, fixture missing){OFF}",
            *self.body(c),
            f"{DIM}E{OFF}",
            # traceback 末行必须指回上面那个测试文件；指向别的文件与 def 行自相矛盾。
            f"{DIM}tests/corpus/test_golden.py:{c.offset % 900 + 40}: AssertionError{OFF}",
            f"{DIM}{f}:{c.offset % 300 + 12}: in {fn}{OFF}",
            f"{DIM}1 failed, {c.i:,} passed, {c.total - c.i - 1:,} deselected "
            f"in {c.elapsed:.2f}s{self._tag(c)}{OFF}",
        ]

    def _tag(self, c):
        return f"  [{c.chapter[:40]}]" if c.chapter else ""


class GitDiff(Theme):
    """一段 → 一个提交里的新增行。正文前缀只占 1 列，是四个主题里正文最宽的。

    `git log -p -1 --skip=N` 实测支持 pull 语义：每敲一次看一个提交的 diff。加行是绿的、
    上下文是灰的，正文天然从噪音里跳出来 —— 不用自己配色。
    """
    key, label, prefix = "git", "git log -p", "+"
    toc_cmd = "git log --oneline --no-decorate -- corpus/"

    def command(self, c):
        return f"git log -p -1 --skip={c.i} -- corpus/"

    def toc_row(self, n, label, off, cols, here):
        mark = f"{CYA}→{OFF}" if here else " "
        rnd = random.Random(off)
        return (f"{mark} {YEL}{'%07x' % rnd.getrandbits(28)}{OFF} "
                f"{label[:max(10, cols - 24)]} {DIM}@{off:06d}{OFF}")

    def typing_target(self, c):
        ch = (c.chapter.split(".")[0].strip() or "00")[:2]
        return f"corpus/ch{ch}.md"

    def body(self, c):
        return [f"{GRN}+{ln}{OFF}" for ln in wrap(c.text, c.cols - 1)]

    def render(self, c):
        f, fn, fn2 = workspace.pick(c.seed)
        name, mail = workspace.git_author()
        # 真 sha 是 40 位十六进制；用偏移做种子保证同一段每次看到同一个 sha。
        rnd = random.Random(c.seed)
        sha = "%040x" % rnd.getrandbits(160)
        path = f"corpus/{(c.chapter[:24] or 'segments').replace(' ', '-').lower()}.md"
        # hunk header 的行数必须和后面实际输出的行数吻合，否则一眼假。
        body = self.body(c)
        ctx = [f"{DIM} Reviewed by {fn} in {f}{OFF}", f"{DIM} {OFF}"]
        old_n, new_n = len(ctx), len(ctx) + len(body)
        start = c.offset % 240 + 8
        return [
            f"{YEL}commit {sha}{OFF}",
            f"{DIM}Author: {name} <{mail}>{OFF}",
            f"{DIM}Date:   {time.strftime('%a %b %d %H:%M:%S %Y %z')}{OFF}",
            "",
            f"{DIM}    corpus: import segment {c.i:05d} ({len(c.text)} chars){OFF}",
            f"{DIM}    Ref: {fn2}(){OFF}",
            "",
            f"{DIM}diff --git a/{path} b/{path}{OFF}",
            f"{DIM}index {sha[:7]}..{sha[7:14]} 100644{OFF}",
            f"{DIM}--- a/{path}{OFF}",
            f"{DIM}+++ b/{path}{OFF}",
            f"{CYA}@@ -{start},{old_n} +{start},{new_n} @@{OFF}",
            ctx[0],
            *body,
            ctx[1],
        ]


class GoTest(Theme):
    """一段 → 一个 go test 的失败输出。前缀是 go 自己的 8 空格缩进，真命令抓过。"""
    key, label, prefix = "go", "go test -v", "        "
    toc_cmd = "go test ./internal/corpus/... -list '.*'"

    def command(self, c):
        return "go test ./internal/corpus/... -run TestGolden -v"

    def toc_row(self, n, label, off, cols, here):
        mark = f"{CYA}→{OFF}" if here else " "
        return (f"{mark} {DIM}TestGolden/part_{n:02d}{OFF} "
                f"{label[:max(10, cols - 34)]} {DIM}@{off:06d}{OFF}")

    def typing_target(self, c):
        return f"internal/corpus/testdata/seg-{c.i:05d}.golden"

    def render(self, c):
        f, fn, _ = workspace.pick(c.seed)
        golden = self.typing_target(c).rsplit("/", 1)[-1]
        return [
            f"{DIM}=== RUN   TestGolden{OFF}",
            f"{DIM}=== RUN   TestGolden/seg_{c.i:05d}{OFF}",
            f"{DIM}    golden_test.go:{c.offset % 300 + 60}: {fn}() mismatch{OFF}",
            f"{DIM}    golden_test.go:{c.offset % 300 + 61}: want {len(c.text)} bytes, "
            f"got 0 ({golden}){OFF}",
            *self.body(c),
            f"{RED}    --- FAIL: TestGolden/seg_{c.i:05d} ({c.elapsed:.2f}s){OFF}",
            f"{RED}--- FAIL: TestGolden ({c.elapsed + 0.01:.2f}s){OFF}",
            f"{DIM}FAIL{OFF}",
            f"{DIM}FAIL\tcorpus/internal/corpus\t{c.elapsed + 0.02:.3f}s"
            f"{('  # ' + c.chapter[:36]) if c.chapter else ''}{OFF}",
        ]


class Ripgrep(Theme):
    """一段 → 一条 ripgrep 命中。前缀是 `%5d:` 定宽行号，靠 --line-number 的真实格式。"""
    key, label, prefix = "rg", "ripgrep", "     :"
    toc_cmd = "rg -c --sort path 'seg-' corpus/"

    def command(self, c):
        return f"rg -n --heading -C0 -m1 'seg-{c.i:05d}' corpus/"

    def toc_row(self, n, label, off, cols, here):
        mark = f"{CYA}→{OFF}" if here else " "
        slug = (label[:24] or "segments").replace(" ", "-").lower()
        return (f"{mark} {CYA}corpus/{slug}.md{OFF}{DIM}:{n + 1}{OFF} "
                f"{label[:max(10, cols - 46)]} {DIM}@{off:06d}{OFF}")

    def typing_target(self, c):
        return f"corpus/notes-{c.i // 100:02d}.md"

    def body(self, c):
        """行号必须逐行递增 —— 定宽靠 %5d，前缀宽度恒为 6。"""
        base = c.offset % 9000 + 12
        return [f"{DIM}{base + n:5d}:{OFF}{ln}"
                for n, ln in enumerate(wrap(c.text, c.cols - 6))]

    def render(self, c):
        f, fn, _ = workspace.pick(c.seed)
        path = f"corpus/{(c.chapter[:24] or 'segments').replace(' ', '-').lower()}.md"
        # 注释符要跟文件扩展名对得上 —— 往 .py 里插 `//` 一眼假。
        cmt = "#" if f.rsplit(".", 1)[-1] in ("py", "rb", "sh", "yml", "yaml") else "//"
        return [
            f"{CYA}{path}{OFF}",
            *self.body(c),
            "",
            f"{DIM}{f}{OFF}",
            f"{DIM}{c.offset % 400 + 20}:  {cmt} TODO({fn}): regenerate golden for "
            f"seg-{c.i:05d}{OFF}",
            "",
            f"{DIM}{c.i + 1} matches ({c.elapsed:.2f}s){OFF}",
        ]


ALL = [Pytest(), GitDiff(), GoTest(), Ripgrep()]
BY_KEY = {t.key: t for t in ALL}


def get(key):
    return BY_KEY.get(key, ALL[0])


def next_key(key):
    keys = [t.key for t in ALL]
    return keys[(keys.index(key) + 1) % len(keys)] if key in keys else keys[0]


def render(theme, c):
    """完整一屏：命令行 + 主题内容。命令行复用真实 PS1，看起来就是我刚敲的。"""
    return "\n".join([f"{c.ps1}{theme.command(c)}", *theme.render(c)])
