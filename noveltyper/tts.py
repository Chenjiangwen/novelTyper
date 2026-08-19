"""语音朗读：可插拔后端 + 磁盘缓存 + 可抢占播放。

三个后端，按 `DEFAULT_ORDER` 取第一个可用的：

  - **edge** —— 微软 Edge 的在线语音，质量最好，需要 `pip install edge-tts`。走它自带的
    命令行而不是 `import edge_tts`：那个库是 asyncio 的，为了一次合成把事件循环引进这个
    纯同步的项目不值得。
  - **say** —— macOS 内置，离线、零依赖、无网络请求，质量一般。
  - **http** —— 自定义 endpoint，配置驱动（见 `config`），响应体直接当音频字节。

**合成必须在后台线程里做。** 主循环阻塞在 `term.read_key` 上，一次网络合成 200-800ms；
放在按键路径上，「按 n 翻页」就变成「按 n 卡一下再翻页」。所以 `speak()` 立即返回，合成
和播放都在线程里，再用 `prefetch()` 把下一段提前合成掉 —— pull 模型下一次按键只走一段，
预取命中率接近 100%。

**抢占用世代号，不能用标志位。** 连按三次 n，前两次的合成线程可能还卡在网络里；它们醒来
后必须发现自己已经过期、不去播放。一个共用的 bool 只能表达「要不要停」，表达不了「谁该
停」—— 第三次 `speak` 刚起的线程会被第一次的 `stop` 误杀，表现成「翻页后没声音」。
"""
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path

CONFIG = Path("tts.json")                                  # 项目内配置，优先
CONFIG_GLOBAL = Path.home() / ".config/noveltyper/tts.json"   # 全局配置
CACHE = Path.home() / ".local/share/noveltyper/tts"
CACHE_MAX = 400                  # 缓存文件数上限，按 mtime 淘汰
DEFAULT_ORDER = ("edge", "say", "http")
TIMEOUT = 20
ERR_MAX = 110                    # 错误摘要长度上限：一行放得下，不能铺满屏

# 播放器按可用性取第一个。`{f}` 是音频路径占位。
PLAYERS = (
    ("afplay", ["afplay", "{f}"]),
    ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "{f}"]),
    ("mpv", ["mpv", "--no-video", "--really-quiet", "{f}"]),
    ("paplay", ["paplay", "{f}"]),
    ("aplay", ["aplay", "-q", "{f}"]),
)


def config_path():
    """按优先级找配置文件：`./tts.json` > `~/.config/noveltyper/tts.json`。

    两级与书架（`./novel_data/` > `~/.local/share/noveltyper/books/`）是同一套约定：
    项目内那份就在手边、改起来看得见，全局那份管所有目录。`./tts.json` 是相对 CWD 的，
    跟 `novel_data` 一样 —— 从别的目录起就只剩全局那份。

    项目内那份在 `.gitignore` 里，`tts.example.json` 才是仓库里的模板：这个文件会长出
    endpoint 和密钥，而仓库是公开的（同 `novel_data/` 的理由）。

    都没有就返回 None —— **配置文件是可选的**，不配也能跑（`available()` 自动挑后端）。
    """
    for p in (CONFIG, CONFIG_GLOBAL):
        if p.is_file():
            return p
    return None


def config():
    """读配置。读不出来就当空的 —— 配置坏了不该让阅读器起不来。"""
    p = config_path()
    if p is None:
        return {}
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def speech_text(s):
    """朗读用的文本：抹掉排版痕迹。

    `book.norm` 把破折号转成了 `--`（为了让打字模式敲得出来），但 TTS 会把它念成
    "dash dash"。折行的换行同理，读出来是断句错位。
    """
    s = s.replace("--", ", ").replace("\n", " ")
    return re.sub(r"\s+", " ", re.sub(r"(,\s*){2,}", ", ", s)).strip(" ,")


def _expand(obj, text, voice):
    """递归替换 `${TEXT}` / `${VOICE}` / `${任意环境变量}`。

    密钥只从环境变量取，**不从配置文件取** —— 配置文件会被备份、被同步、被误提交，
    而这个项目已经有「progress.json 不存明文正文」的同类先例。
    """
    if isinstance(obj, str):
        out = obj.replace("${TEXT}", text).replace("${VOICE}", voice)
        for name in set(re.findall(r"\$\{(\w+)\}", out)):
            out = out.replace("${" + name + "}", os.environ.get(name, ""))
        return out
    if isinstance(obj, dict):
        return {k: _expand(v, text, voice) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v, text, voice) for v in obj]
    return obj


def _edge(text, cfg, out):
    """微软 Edge 语音。走 CLI 而不是 `import edge_tts` —— 那个库是 asyncio 的。

    **rate/volume/pitch 必须拼成 `--rate=-10%` 单个 argv，不能分成两个。** 这三个值最常
    见的写法就是负数（`"-10%"` 放慢语速），而 `["--rate", "-10%"]` 会让 edge-tts 的
    argparse 把 `-10%` 当成一个 flag 并报 `expected one argument` → 退非零 → 整条链路
    静音。`--rate=-10%` 形式下 argparse 不做这个猜测。正数值（`"+0%"`）碰不到这个坑，
    所以只在配了负值时才复现，表现成「按 v 有 `[tts] edge` 但没声音」。
    """
    exe = shutil.which("edge-tts")
    if not exe:
        return None
    voice = cfg.get("voice") or "en-US-AriaNeural"
    cmd = [exe, "--voice", voice, "--text", text, "--write-media", str(out)]
    for k, flag in (("rate", "--rate"), ("volume", "--volume"), ("pitch", "--pitch")):
        if cfg.get(k):
            cmd.append(f"{flag}={cfg[k]}")
    r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)
    return out if r.returncode == 0 and out.exists() and out.stat().st_size else None


def _say(text, cfg, out):
    """macOS 内置。`say` 只写 aiff/caf —— 别给它 .mp3 后缀，afplay 会按后缀猜错。

    不要加 `--data-format` 省体积：实测 `--data-format=LEI16@22050` 会被拒
    （`Opening output file failed: fmt?`）并留下 0 字节文件。默认 aiff 就能放。
    """
    exe = shutil.which("say")
    if not exe:
        return None
    dst = out.with_suffix(".aiff")
    cmd = [exe, "-o", str(dst)]
    if cfg.get("voice"):
        cmd += ["-v", cfg["voice"]]
    if cfg.get("rate"):
        cmd += ["-r", str(cfg["rate"])]
    r = subprocess.run(cmd + [text], capture_output=True, timeout=TIMEOUT)
    return dst if r.returncode == 0 and dst.exists() and dst.stat().st_size else None


def _http(text, cfg, out):
    """自定义 endpoint。响应体直接当音频字节 —— 覆盖 OpenAI 兼容、Azure、本地 Piper。

    配置示例（`./tts.json` 或 `~/.config/noveltyper/tts.json`）：
        {"backend": "http", "http": {
           "url": "https://api.example.com/v1/audio/speech",
           "headers": {"Authorization": "Bearer ${TTS_KEY}"},
           "body": {"model": "tts-1", "input": "${TEXT}", "voice": "alloy"},
           "audio": "mp3"}}
    """
    url = cfg.get("url")
    if not url:
        return None
    voice = cfg.get("voice") or ""
    body = cfg.get("body")
    data = (json.dumps(_expand(body, text, voice)).encode()
            if body is not None else _expand(cfg.get("data", "${TEXT}"), text, voice).encode())
    headers = {"Content-Type": "application/json", **_expand(cfg.get("headers", {}), text, voice)}
    req = urllib.request.Request(_expand(url, text, voice), data=data,
                                 headers=headers, method=cfg.get("method", "POST"))
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:   # noqa: S310 - URL 来自用户配置
        blob = r.read()
    if not blob:
        return None
    dst = out.with_suffix("." + cfg.get("audio", "mp3").lstrip("."))
    dst.write_bytes(blob)
    return dst


BACKENDS = {"edge": _edge, "say": _say, "http": _http}


def err_summary(e):
    """异常 → 一行短摘要。**绝不能把后端的原始命令行放进去。**

    `edge` 后端是当 CLI 调的，而 `subprocess.TimeoutExpired.__str__` 会把完整 argv 塞进
    消息里 —— 其中 `--text` 的值就是**整段小说正文**。一次超时就能在屏幕上打出四百字英文
    小说，伪装当场崩掉；这和「progress.json 不存明文正文」是同一条理由。所以对带 `cmd`
    的子进程异常只取超时秒数，绝不碰 `str(e)`。

    其余异常（网络、HTTP 状态码）的 str 里没有正文，照原样取，但仍要截断 —— 错误跟在
    `[tts] ...` 后面同一行，铺满屏幕本身就是穿帮面。
    """
    if isinstance(e, subprocess.TimeoutExpired):
        return f"timed out after {e.timeout:.0f}s"
    if isinstance(e, subprocess.CalledProcessError):
        return f"exited {e.returncode}"
    return re.sub(r"\s+", " ", str(e))[:ERR_MAX]


def available():
    """按 DEFAULT_ORDER 可用的后端名。http 只在配了 url 时算可用。"""
    cfg = config()
    out = []
    for name in DEFAULT_ORDER:
        if name == "http":
            if (cfg.get("http") or {}).get("url"):
                out.append(name)
        elif shutil.which("edge-tts" if name == "edge" else name):
            out.append(name)
    return out


def _player():
    for name, cmd in PLAYERS:
        if shutil.which(name):
            return cmd
    return None


def _trim_cache():
    files = sorted(CACHE.glob("*.*"), key=lambda p: p.stat().st_mtime)
    for p in files[:-CACHE_MAX]:
        with contextlib.suppress(OSError):
            p.unlink()


class Engine:
    """一个朗读引擎。`speak` 立即返回，合成与播放都在后台线程。

    **世代号是抢占的全部机制。** 每次 `speak`/`stop` 都 +1；线程在每个可能长耗时的步骤
    后重新核对自己的代号，过期就安静退出。见模块 docstring 里为什么不能用 bool。
    """

    def __init__(self, enabled=False, backend=""):
        self.enabled = enabled
        self.cfg = config()
        # 优先级：调用方（按书存的偏好）> 配置文件 > 第一个可用的。中间这一档不能省 ——
        # `_http` 的 docstring 就是拿 `{"backend": "http", ...}` 当示例写给用户看的，
        # 只按 available() 取首个的话，装了 edge-tts 的机器永远选不到自定义 endpoint。
        want = backend or self.cfg.get("backend") or ""
        self.backend = want if want in BACKENDS else (available() or [""])[0]
        self.gen = 0
        self.err = ""
        self._lock = threading.Lock()
        self._proc = None
        self._pending = {}            # 预取：cache key → 线程

    # ---- 错误状态 ------------------------------------------------------
    # `err` 是**当前状态**而不是日志，两条规则：
    #   - 一次成功的合成就把它清掉。不清的话按 `v` 会一直打十分钟前那次超时，看起来像
    #     "刚刚又失败了"，而声音其实是好的 —— 把人往错的方向引。
    #   - 读走即清（`take_err`）。同一次失败只报一次；否则连按两下 `v`（关再开）就会把
    #     同一条错误打两遍，屏幕上像出了两次问题。
    def _ok(self):
        with self._lock:
            self.err = ""

    def _fail(self, msg):
        with self._lock:
            self.err = msg

    def take_err(self):
        """取出并清空当前错误 —— 报一次就算报过了。"""
        with self._lock:
            e, self.err = self.err, ""
        return e

    # ---- 缓存 ----------------------------------------------------------
    def _key(self, text):
        cfg = self._bcfg()
        sig = f"{self.backend}|{cfg.get('voice','')}|{cfg.get('rate','')}|{text}"
        return hashlib.sha256(sig.encode()).hexdigest()[:32]

    def _bcfg(self):
        return self.cfg.get(self.backend) or {}

    def _cached(self, key):
        for p in CACHE.glob(key + ".*"):
            if p.stat().st_size:
                return p
        return None

    def _synth(self, text):
        """合成（命中缓存就直接返回）。返回音频路径或 None。线程安全。"""
        key = self._key(text)
        hit = self._cached(key)
        if hit:
            self._ok()
            return hit
        CACHE.mkdir(parents=True, exist_ok=True)
        fn = BACKENDS.get(self.backend)
        if not fn:
            self._fail("no backend (install edge-tts, or configure http)")
            return None
        tmp = Path(tempfile.mkdtemp(dir=CACHE, prefix=".synth-"))
        try:
            got = fn(text, self._bcfg(), tmp / "a.mp3")
            if not got:
                # 后端不抛、只返回 None（edge-tts 退非零、say 写了 0 字节、endpoint 返回
                # 空体）。这条分支不留话的话，那次「按 v 有 `[tts] edge` 但没声音」就完全
                # 无从下手 —— `--rate=-10%` 那个 bug 当初就是这个形态。
                self._fail(f"{self.backend} produced no audio")
                return None
            dst = CACHE / (key + got.suffix)
            os.replace(got, dst)      # 原子落盘：半个文件被播放器读到就是杂音
            _trim_cache()
            self._ok()
            return dst
        except Exception as e:        # noqa: BLE001 - 网络/子进程失败形态太多
            self._fail(err_summary(e))
            return None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- 播放 ----------------------------------------------------------
    def stop(self):
        """静音并作废所有在飞的合成。老板键、退出、切段都要调。"""
        with self._lock:
            self.gen += 1
            proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            with contextlib.suppress(OSError, ProcessLookupError):
                proc.terminate()

    def _play(self, path, gen):
        cmd = _player()
        if not cmd:
            self._fail("no audio player (afplay/ffplay/mpv/aplay)")
            return
        argv = [a.replace("{f}", str(path)) for a in cmd]
        with self._lock:
            if gen != self.gen:      # 合成期间被抢占了
                return
            try:
                self._proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
            except OSError as e:
                self.err = err_summary(e)   # 直接赋值：锁已在手上，`_fail` 会自死锁
                return
            proc = self._proc
        with contextlib.suppress(OSError):
            proc.wait()
        with self._lock:
            if self._proc is proc:
                self._proc = None

    def speak(self, text):
        """朗读一段。抢占上一段，不阻塞调用方。"""
        if not self.enabled:
            return
        text = speech_text(text)
        if not text:
            return
        self.stop()                  # 先抢占，gen 已 +1
        with self._lock:
            gen = self.gen
        threading.Thread(target=self._run, args=(text, gen), daemon=True).start()

    def _run(self, text, gen):
        path = self._synth(text)
        if path and gen == self.gen:
            self._play(path, gen)

    def prefetch(self, text):
        """后台合成但不播放 —— 下一段的音频提前备好，翻页就不用等网络。"""
        if not self.enabled:
            return
        text = speech_text(text)
        if not text or self._cached(self._key(text)):
            return
        key = self._key(text)
        t = self._pending.get(key)
        if t and t.is_alive():
            return
        self._pending = {k: v for k, v in self._pending.items() if v.is_alive()}
        t = threading.Thread(target=self._synth, args=(text,), daemon=True)
        self._pending[key] = t
        t.start()

    def label(self):
        return f"{self.backend or 'none'}{'' if self.enabled else ' (off)'}"


