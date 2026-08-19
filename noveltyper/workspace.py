"""扫当前工作目录，给伪装输出提供**真实**的文件名与符号名。

fish-reader 的一条核心经验：第一屏必须真。编造的 `tests/corpus/test_golden.py` 只要
老板顺手 `ls` 一下就露了；指向真实存在的文件则完全经得起追问。

配额是为启动速度设的 —— 懒加载 + 上限，扫不到就退回一组通用名字，绝不因为目录太大
而卡住。
"""
import functools
import os
import re
import random
import subprocess
from pathlib import Path

EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rs", ".c", ".cpp", ".h")
SKIP = {"node_modules", "dist", "out", "build", ".git", "vendor", "target",
        "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "novel_data"}
MAX_FILES = 200          # 目录再大也就扫这么多
MAX_READ = 24            # 其中只读这些个的内容提取符号
MAX_BYTES = 200_000      # 单文件超过就跳过，避免读进来一个压缩包似的巨文件

CLASS_RE = re.compile(r"^\s*(?:class|interface|struct|enum)\s+([A-Z]\w+)", re.M)
FUNC_RE = re.compile(r"^\s*(?:def|func|function|fn)\s+([a-zA-Z]\w{2,})", re.M)
RESERVED = {"main", "init", "test", "setup", "new", "get", "set", "run", "self"}
MAX_SYM = 24             # 符号名上限：伪装行是一行，长名字会把行顶出终端宽度

FALLBACK_FILES = ["src/parser.py", "src/handler.py", "lib/client.py",
                  "internal/store.go", "src/utils/format.ts"]
FALLBACK_SYMS = ["parse_header", "normalize", "load_config", "flush_buffer",
                 "resolve_path", "encode_frame"]


@functools.cache
def scan(root=None):
    """(相对路径列表, 符号名列表)。结果缓存 —— 一次会话内目录不会变。"""
    root = Path(root or Path.cwd())
    files = []
    for dirpath, dirnames, names in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP and not d.startswith(".")]
        for n in names:
            if n.endswith(EXTS):
                files.append(Path(dirpath) / n)
        if len(files) >= MAX_FILES:
            break

    syms = []
    for f in files[:MAX_READ]:
        try:
            if f.stat().st_size > MAX_BYTES:
                continue
            src = f.read_text(errors="replace")
        except OSError:
            continue
        syms += CLASS_RE.findall(src) + FUNC_RE.findall(src)

    rel = [str(f.relative_to(root)) for f in files] or FALLBACK_FILES
    # 长度上限不是洁癖：符号名嵌在一行伪装里（`TODO(name): ...`），扫到本仓库自己的
    # `test_whole_render_fits_at_usable_widths` 这种 39 字符名字就会把整行顶超宽。
    syms = sorted({s for s in syms
                   if s.lower() not in RESERVED and len(s) <= MAX_SYM})
    return rel, syms or FALLBACK_SYMS


@functools.cache
def git_author():
    """真实 git 身份 —— git 主题的 Author 行编不出比这更可信的。"""
    out = []
    for k in ("user.name", "user.email"):
        try:
            p = subprocess.run(["git", "config", "--get", k],
                               capture_output=True, text=True, timeout=3)
            out.append(p.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            out.append("")
    name = out[0] or os.environ.get("USER", "dev")
    return name, out[1] or f"{name}@localhost"


def pick(seed, root=None):
    """由种子确定性地选一组 (文件, 符号, 符号2)。

    **必须确定性**：同一段前后翻两次，伪装里的文件名要一样。随机会让人发现"这段代码
    每次报错的文件都不同"，比编造的文件名更可疑。
    """
    files, syms = scan(root)
    r = random.Random(seed)
    return (r.choice(files), r.choice(syms),
            r.choice(syms) if len(syms) > 1 else syms[0])
