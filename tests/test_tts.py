"""TTS 的四条规则 —— 全部不碰网络、不出声。

后端用假的（往 monkeypatch 进 `BACKENDS`），播放器也 stub 掉：真跑 `say` 会让测试慢
一个数量级，真发 HTTP 请求会让测试依赖网络。这里要钉的是**编排**而不是后端本身：
抢占语义、缓存键、密钥不落盘、配置占位替换。
"""
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from noveltyper import tts


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """把缓存和配置都挪进 tmp —— 否则测试会污染真实缓存目录。

    **两级配置都要挪。** 只挪 `CONFIG` 的话，本机真实的全局配置（或仓库根的 tts.json）
    会被 `config_path()` 找到，测试就依赖开发机的配置了。
    """
    monkeypatch.setattr(tts, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(tts, "CONFIG", tmp_path / "tts.json")
    monkeypatch.setattr(tts, "CONFIG_GLOBAL", tmp_path / "absent-global.json")
    monkeypatch.setattr(tts, "_player", lambda: None)   # 默认不出声
    return tmp_path


def fake_backend(calls, delay=0.0, ok=True):
    def fn(text, cfg, out):
        calls.append(text)
        if delay:
            time.sleep(delay)
        if not ok:
            return None
        dst = out.with_suffix(".mp3")
        dst.write_bytes(b"ID3" + text.encode()[:8])
        return dst
    return fn


def test_speech_text_strips_typography():
    """朗读文本要抹掉排版痕迹 —— `book.norm` 把破折号转成 `--` 是为了让打字模式敲得出来，
    但 TTS 会把它念成 "dash dash"。折行的换行同理，读出来是断句错位。"""
    assert tts.speech_text("He waited -- then she left.") == "He waited , then she left."
    assert tts.speech_text("one\ntwo   three") == "one two three"
    assert tts.speech_text("--") == ""


def test_synth_caches_by_content(monkeypatch):
    """同一段文本只合成一次 —— 来回翻页（n/p）是常见操作，每次都重新合成既慢又费配额。"""
    calls = []
    monkeypatch.setitem(tts.BACKENDS, "fake", fake_backend(calls))
    e = tts.Engine(enabled=True, backend="fake")
    a = e._synth("hello there")
    b = e._synth("hello there")
    assert a == b and len(calls) == 1


def test_cache_key_includes_voice(monkeypatch):
    """换了嗓音必须重新合成 —— 缓存键只含文本的话，切 voice 后放出来还是旧声音。"""
    monkeypatch.setitem(tts.BACKENDS, "fake", fake_backend([]))
    e = tts.Engine(enabled=True, backend="fake")
    k1 = e._key("x")
    e.cfg = {"fake": {"voice": "Aria"}}
    assert e._key("x") != k1


def test_stop_invalidates_in_flight_synthesis(monkeypatch):
    """**抢占必须让在飞的合成放弃播放。**

    连按三次 n，前两次的合成线程可能还卡在网络里；它们醒来后必须发现自己已经过期。
    这条钉住世代号语义：`stop()` 之后，之前起的线程不许再走到播放。
    """
    played = []
    monkeypatch.setitem(tts.BACKENDS, "fake", fake_backend([], delay=0.15))
    monkeypatch.setattr(tts.Engine, "_play",
                        lambda self, path, gen: played.append((path, gen)))
    e = tts.Engine(enabled=True, backend="fake")
    e.speak("first")
    time.sleep(0.03)                 # 合成还没完
    e.stop()                         # 抢占
    time.sleep(0.3)                  # 等那个线程醒来
    assert played == []              # 醒来发现过期，没播


def test_speak_preempts_previous(monkeypatch):
    """后一段抢占前一段 —— 快速翻页时不能两段声音叠在一起。"""
    played = []
    monkeypatch.setitem(tts.BACKENDS, "fake", fake_backend([], delay=0.15))
    monkeypatch.setattr(tts.Engine, "_play",
                        lambda self, path, gen: played.append(gen))
    e = tts.Engine(enabled=True, backend="fake")
    e.speak("first")
    time.sleep(0.03)
    e.speak("second")
    time.sleep(0.4)
    assert len(played) == 1          # 只有后一次活下来
    assert played[0] == e.gen


def test_speak_is_non_blocking(monkeypatch):
    """**合成不能在按键路径上。** 主循环阻塞在 read_key，一次网络合成 200-800ms；
    放在同步路径上「按 n 翻页」就变成「按 n 卡一下再翻页」。"""
    monkeypatch.setitem(tts.BACKENDS, "fake", fake_backend([], delay=0.4))
    e = tts.Engine(enabled=True, backend="fake")
    t0 = time.monotonic()
    e.speak("some paragraph")
    assert time.monotonic() - t0 < 0.1


def test_disabled_engine_does_nothing(monkeypatch):
    calls = []
    monkeypatch.setitem(tts.BACKENDS, "fake", fake_backend(calls))
    e = tts.Engine(enabled=False, backend="fake")
    e.speak("x")
    e.prefetch("y")
    time.sleep(0.1)
    assert calls == []


def test_prefetch_warms_cache_then_speak_hits_it(monkeypatch):
    """预取的意义：翻页时音频已经在盘上。"""
    calls = []
    monkeypatch.setitem(tts.BACKENDS, "fake", fake_backend(calls))
    e = tts.Engine(enabled=True, backend="fake")
    e.prefetch("next paragraph")
    for _ in range(50):
        if e._cached(e._key("next paragraph")):
            break
        time.sleep(0.02)
    assert e._cached(e._key("next paragraph")) is not None
    assert len(calls) == 1
    e.prefetch("next paragraph")     # 已在缓存，不该再合成
    time.sleep(0.1)
    assert len(calls) == 1


def test_edge_joins_rate_with_equals_so_negative_values_survive(isolate, monkeypatch):
    """**`--rate=-10%` 必须是一个 argv，不能拆成 `["--rate", "-10%"]`。**

    放慢语速的写法天然是负数，而 argparse 见到独立的 `-10%` 会当它是个 flag 并报
    `expected one argument` → edge-tts 退非零 → `_edge` 返回 None → 静音。正数值
    （`"+0%"`）碰不到，所以只在配了负 rate 时复现，表现成「有 `[tts] edge` 但没声音」。
    """
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        Path(cmd[cmd.index("--write-media") + 1]).write_bytes(b"ID3")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(tts.shutil, "which", lambda n: "/usr/bin/edge-tts")
    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    cfg = {"voice": "en-US-AndrewNeural", "rate": "-10%", "volume": "+0%", "pitch": "+0Hz"}
    assert tts._edge("hello", cfg, isolate / "a.mp3") is not None
    assert "--rate=-10%" in seen["cmd"]
    assert "-10%" not in seen["cmd"]          # 独立的负值 token 就是那个 bug
    assert "--volume=+0%" in seen["cmd"] and "--pitch=+0Hz" in seen["cmd"]


def test_backend_failure_is_recorded_not_raised(monkeypatch):
    """后端挂了要记在 `err` 里，**不能抛** —— 网络抽一下不该把阅读器带崩。"""
    def boom(text, cfg, out):
        raise OSError("network unreachable")
    monkeypatch.setitem(tts.BACKENDS, "fake", boom)
    e = tts.Engine(enabled=True, backend="fake")
    assert e._synth("x") is None
    assert "network unreachable" in e.err


def test_http_secrets_come_from_env_not_config(monkeypatch, isolate):
    """**密钥只从环境变量取。** 配置文件会被备份、被同步、被误提交 —— 这个项目已经有
    「progress.json 不存明文正文」的同类先例。"""
    monkeypatch.setenv("MY_TTS_KEY", "sk-secret-123")
    body = tts._expand({"input": "${TEXT}", "key": "${MY_TTS_KEY}"}, "hello", "v1")
    assert body == {"input": "hello", "key": "sk-secret-123"}
    assert "sk-secret" not in json.dumps({"headers": {"Authorization": "Bearer ${MY_TTS_KEY}"}})


def test_expand_leaves_unknown_env_empty(monkeypatch):
    """没设的环境变量替成空串，不要把 `${FOO}` 原样发出去 —— 那会被当成明文密钥。"""
    monkeypatch.delenv("NT_ABSENT", raising=False)
    assert tts._expand("Bearer ${NT_ABSENT}", "t", "v") == "Bearer "


def test_config_survives_broken_json(isolate):
    """配置坏了当空的 —— 不该因为 tts.json 少个逗号就起不来。"""
    tts.CONFIG.write_text("{not json")
    assert tts.config() == {}


def test_project_config_beats_global(isolate, monkeypatch):
    """**项目内的 `./tts.json` 优先于全局。** 与书架（`./novel_data/` 优先于
    `~/.local/share/.../books/`）是同一套约定：项目里那份跟着仓库走、改起来看得见。"""
    g = isolate / "global.json"
    g.write_text(json.dumps({"backend": "say"}))
    monkeypatch.setattr(tts, "CONFIG_GLOBAL", g)
    assert tts.config()["backend"] == "say"          # 只有全局时用全局
    tts.CONFIG.write_text(json.dumps({"backend": "http"}))
    assert tts.config()["backend"] == "http"
    assert tts.config_path() == tts.CONFIG


def test_no_config_anywhere_is_not_an_error(isolate, monkeypatch):
    """**配置文件是可选的。** 两处都没有时要静静地返回空 dict —— 首次运行就是这个状态，
    此时靠 `available()` 自动挑后端，不能因为缺文件就报错或哑掉。"""
    assert tts.config_path() is None
    assert tts.config() == {}
    monkeypatch.setattr(tts.shutil, "which", lambda n: "/usr/bin/say" if n == "say" else None)
    assert tts.Engine(enabled=True).backend == "say"


def test_available_requires_url_for_http(isolate, monkeypatch):
    """http 后端没配 url 就不算可用 —— 否则它会被选中然后每次都静默失败。"""
    monkeypatch.setattr(tts.shutil, "which", lambda n: None)
    tts.CONFIG.write_text(json.dumps({"http": {}}))
    assert "http" not in tts.available()
    tts.CONFIG.write_text(json.dumps({"http": {"url": "https://x/y"}}))
    assert tts.available() == ["http"]


def test_config_backend_beats_autodetect(isolate, monkeypatch):
    """**配置里写了 backend 就得听它。** `_http` 的 docstring 拿 `{"backend": "http"}`
    当示例；只按 available() 取首个的话，装了 edge-tts 的机器永远选不到自定义 endpoint。"""
    monkeypatch.setattr(tts.shutil, "which", lambda n: "/usr/bin/" + n)   # edge 也可用
    tts.CONFIG.write_text(json.dumps({"backend": "http",
                                      "http": {"url": "https://x/y"}}))
    assert tts.Engine().backend == "http"


def test_saved_pref_beats_config(isolate, monkeypatch):
    """按书存的偏好优先于配置默认值 —— 一本书听 edge、另一本听本地 say 是常态。"""
    monkeypatch.setattr(tts.shutil, "which", lambda n: "/usr/bin/" + n)
    tts.CONFIG.write_text(json.dumps({"backend": "http",
                                      "http": {"url": "https://x/y"}}))
    assert tts.Engine(backend="say").backend == "say"


def test_unknown_backend_falls_back_instead_of_going_mute(isolate, monkeypatch):
    """存档里的后端名失效了（卸了 edge-tts、改了配置）要退到可用的那个，不能静默哑掉。"""
    monkeypatch.setattr(tts.shutil, "which", lambda n: "/usr/bin/say" if n == "say" else None)
    assert tts.Engine(backend="no-such-backend").backend == "say"


def test_cache_trimmed_to_limit(isolate, monkeypatch):
    """缓存要有上限 —— 一本书几千段，无上限就是往 ~/.local/share 里堆几个 G。"""
    monkeypatch.setattr(tts, "CACHE_MAX", 3)
    tts.CACHE.mkdir(parents=True)
    for n in range(6):
        p = tts.CACHE / f"{n}.mp3"
        p.write_bytes(b"x")
        import os
        os.utime(p, (n, n))          # 按 mtime 淘汰最旧的
    tts._trim_cache()
    left = sorted(p.stem for p in tts.CACHE.glob("*.mp3"))
    assert left == ["3", "4", "5"]


def test_stop_without_player_is_safe():
    """没装播放器时 stop/speak 都不能炸 —— Linux 上很可能一个都没有。"""
    e = tts.Engine(enabled=True, backend="say")
    e.stop()
    e.stop()


def test_no_thread_leak_on_repeated_prefetch(monkeypatch):
    """反复预取不该攒线程 —— 读一小时就是几百次翻页。"""
    monkeypatch.setitem(tts.BACKENDS, "fake", fake_backend([]))
    e = tts.Engine(enabled=True, backend="fake")
    before = threading.active_count()
    for n in range(30):
        e.prefetch(f"paragraph {n}")
    time.sleep(0.5)
    assert threading.active_count() <= before + 5


def test_http_backend_end_to_end_against_local_server(isolate, monkeypatch):
    """**自定义 endpoint 走一遍真 HTTP** —— 起一个本地 server，不碰外网。

    这条钉的是「配置怎么变成一个请求」：url/headers/body 三处都要做占位替换，密钥从环境
    变量来，响应体原样落盘并按 `audio` 定后缀。这三步任一错了都只表现成「没声音」，
    看不出是哪一步 —— 所以要在这里断言到请求本身。
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["auth"] = self.headers.get("Authorization")
            seen["ctype"] = self.headers.get("Content-Type")
            seen["body"] = json.loads(
                self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.end_headers()
            self.wfile.write(b"RIFFfake-audio-bytes")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("NT_TEST_KEY", "sk-from-env")
        tts.CONFIG.write_text(json.dumps({"backend": "http", "http": {
            "url": f"http://127.0.0.1:{srv.server_port}/v1/audio/speech",
            "headers": {"Authorization": "Bearer ${NT_TEST_KEY}"},
            "body": {"model": "tts-1", "input": "${TEXT}", "voice": "${VOICE}"},
            "voice": "alloy",
            "audio": "wav"}}))
        e = tts.Engine(enabled=True)
        assert e.backend == "http"
        got = e._synth("hello world")
        assert got is not None, e.err
        assert got.suffix == ".wav"                 # 后缀跟着 audio 走，不硬编码 mp3
        assert got.read_bytes() == b"RIFFfake-audio-bytes"
        assert seen["path"] == "/v1/audio/speech"
        assert seen["auth"] == "Bearer sk-from-env"  # 密钥来自环境变量
        assert seen["ctype"] == "application/json"
        assert seen["body"] == {"model": "tts-1", "input": "hello world",
                                "voice": "alloy"}
        assert "${" not in json.dumps(seen["body"])  # 没有占位符漏出去
    finally:
        srv.shutdown()


def test_http_error_is_recorded_not_raised(isolate, monkeypatch):
    """endpoint 返回 500 时记进 `err`、返回 None —— 不能把阅读器带崩。"""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(500)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        tts.CONFIG.write_text(json.dumps({"backend": "http", "http": {
            "url": f"http://127.0.0.1:{srv.server_port}/x"}}))
        e = tts.Engine(enabled=True)
        assert e._synth("hi") is None
        assert "500" in e.err
    finally:
        srv.shutdown()
