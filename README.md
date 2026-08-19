# novelTyper

> 在终端里读英文小说、顺便练打字 —— 而屏幕上看起来是一堆构建输出。

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-106%20passed-brightgreen.svg)](#开发)

novelTyper 把 epub 小说渲染成 `git log -p`、`pytest`、`go test`、`ripgrep` 的输出形态，
一次按键推进一段正文。它不接管屏幕，而是内联进你当前的终端会话：复用机器上真实的 PS1，
第一屏引用的文件名、符号名、git 身份都取自当前工作目录。

```
dev@mac corpus % git log -p -1 --skip=162 -- corpus/
commit 3e9c06e911c48a08fcfc606bc84a76077d1977f9
Author: chenjiangwen <techwen@qq.com>
Date:   Wed Aug 19 16:28:03 2026 +0800

    corpus: import segment 00162 (202 chars)
    Ref: pick()

diff --git a/corpus/3.-red-coast-i.md b/corpus/3.-red-coast-i.md
index 3e9c06e..911c48a 100644
--- a/corpus/3.-red-coast-i.md
+++ b/corpus/3.-red-coast-i.md
@@ -89,2 +89,5 @@
 Reviewed by Theme in tests/test_term.py
+The lamp on the far bank went out a little before dawn, and for a while there was
+nothing to look at but the water. She waited without impatience, the way one waits for
+a train that is known to be late.
```

> 上面的正文是占位文字，不是书里的段落 —— README 会跟着仓库公开。

## 目录

- [特性](#特性)
- [安装](#安装)
- [快速开始](#快速开始)
- [用法](#用法)
  - [书架与切换](#书架与切换)
  - [按键](#按键)
  - [主题](#主题)
  - [打字模式](#打字模式)
  - [老板键](#老板键)
- [进度存档](#进度存档)
- [工作原理](#工作原理)
- [开发](#开发)
- [FAQ](#faq)

## 特性

- **伪装渲染** —— 四套主题（pytest / git / go / rg），正文嵌在真实工具的输出格式里，细节
  按真实行为写死（40 位 sha、对得上扩展名的注释符、从段序号线性导出的耗时）。
- **不接管屏幕** —— 内联进当前会话，一次按键出一段，滚回去还能看到你真正的命令历史。
- **打字练习** —— 任意段落按 `t` 进入，逐字符比对，统计净 wpm 与准确率。
- **老板键** —— Esc 单击切到 `tsc --watch --noEmit` 的等待屏，`Ctrl-L` 恢复。
- **多书书架** —— epub 丢进目录即可，进度按书独立记录。
- **章节目录** —— 从 `toc.ncx` 解析，按序号跳转。
- **零配置** —— 唯一依赖是 `lxml`，无配置文件、无缓存、无索引。

## 安装

需要 Python 3.9+。

```bash
git clone https://github.com/Chenjiangwen/novelTyper.git
cd novelTyper
pip install -e .
```

也可以不安装直接运行：`python3 corpus_verify.py`。

> 命令名是 `corpus-verify` 而不是 `noveltyper`。`ps aux`、shell 补全和 `.zsh_history`
> 都是穿帮面；终端标题同样被改写成 `tsc --watch`。

## 快速开始

```bash
mkdir -p novel_data
cp ~/Downloads/some-novel.epub novel_data/
corpus-verify
```

按 `n` 或空格推进，`t` 练打字，`q` 退出并存档。

## 用法

```
corpus-verify [BOOK]

BOOK  epub 路径，或书名的一部分（大小写不敏感）。省略时打开书架上的第一本。
```

### 书架与切换

导入一本书就是把 epub 放进书架目录，没有导入步骤。两个位置按优先级查找：

| 位置 | 用途 |
|---|---|
| `./novel_data/*.epub` | 项目内书架，优先 |
| `~/.local/share/noveltyper/books/*.epub` | 全局书架 |

书架上有多本时用子串指定，不必敲完整文件名：

```bash
corpus-verify              # 书架第一本（目录优先级 + 文件名排序）
corpus-verify dark         # → The Dark Forest (Cixin Liu).epub
corpus-verify ~/x.epub     # 存在的路径直接使用
```

支持子串而非只收路径是有意的：命令行参数本身是穿帮面，`.zsh_history` 里留一行
`corpus-verify "The Dark Forest (Cixin Liu).epub"` 就白干了，而 `corpus-verify dark`
看着像个构建目标。子串匹配到多本时列出候选并退出，不猜 —— 开错书会把进度记到另一本
书名下，等发现时两边的偏移都已经脏了。

### 按键

| 键 | 作用 |
|---|---|
| `Enter` / `n` / `空格` / `j` | 下一段 |
| `p` / `k` | 上一段 |
| `s` | 切换主题 |
| `c` | 章节目录，输入序号 + 回车跳转 |
| `t` | 对当前段进入打字模式 |
| `Esc` | 老板键 |
| `Ctrl-L` | 从老板键恢复 |
| `q` / `Ctrl-D` | 退出并存档 |

推进键给了四个 —— 读顺的时候手不该去找特定键。其余可打印键一律回一句
`zsh: command not found: X`，误按也留在伪装里，不会冒出「未知按键」这种只有阅读器才会
说的话。方向键被静默吞掉：它的首字节就是 Esc，而 Esc 单击即进老板键。

### 主题

`s` 键循环切换，选择写进存档，下次启动沿用。目录页和打字模式的伪装路径都跟着主题变。

|  |  |
|:--|:--|
| **pytest** —— golden fixture 比对失败，正文在 `E ` 前缀后 | **git** —— `git log -p` 的 diff，`+` 前缀只占 1 列，正文能到 99 列 |
| ![pytest 主题](docs/screenshots/theme_pytest.png) | ![git 主题](docs/screenshots/theme_git.png) |
| **go** —— `go test -v` 的日志，8 空格缩进 | **rg** —— ripgrep 输出，`%5d:` 定宽行号 |
| ![go 主题](docs/screenshots/theme_go.png) | ![rg 主题](docs/screenshots/theme_rg.png) |

缩到两列后字很小，点开看原图。

除 git 主题带真实时间戳外，同一段渲染两次逐字节相同 —— 「每次报错的文件都不一样」比
编造的文件名更可疑。

### 打字模式

`t` 进入，伪装成往 golden fixture 写内容的 heredoc（`cat > path <<'EOF'`）。Esc 或
Ctrl-C 放弃本段。

![打字模式](docs/screenshots/typer.png)

绿色是已敲对的部分，光标停在当前字符，后面是灰色 ghost。退格只回看，不改计数。
结束后给出 `净 wpm`（`60 × 敲对字符数 / 秒 / 5`）和准确率。

键盘敲不出来的字符自动跳过，不计击键也不计错。

### 老板键

Esc 单击切到 `tsc --watch --noEmit` 的等待屏 —— watch 语义天然解释了「这个终端为什么
停着不动」。`Ctrl-L` 恢复，且**绝不自动恢复**。

伪装屏里的 Esc 是故意不响应的，详见 [工作原理](#工作原理)。

## 进度存档

原子写入 `~/.local/share/noveltyper/progress.json`，**按书名（epub 文件名）分条**，每条
记 `offset / typed / wpm / theme`：

```json
{
  "_v": 2,
  "The Three-Body Problem (Cixin Liu).epub": {
    "offset": 34095, "typed": 0, "wpm": 0.0, "theme": "git"
  },
  "Ball Lightning (Liu Cixin).epub": {
    "offset": 3275, "typed": 2, "wpm": 19.3, "theme": "git"
  }
}
```

几本书轮着读，各自的位置、打字次数、最佳 wpm 和主题互不影响。存档里**不写明文正文**
—— 它躺在 `~/.local/share` 里，谁都可能看到。

## 工作原理

```
noveltyper/
├── app.py         主循环：按键分发、书架定位、退出时存档
├── book.py        epub → 扁平文本 + 段落区间 + 章节索引
├── segments.py    段落合并成阅读单元
├── themes.py      四套伪装渲染器
├── typemode.py    打字模式
├── panic.py       老板键
├── toc.py         章节目录
├── state.py       进度存档（原子写、v1 迁移）
├── term.py        cbreak、按键读取、转义序列消歧
└── workspace.py   扫当前目录，为伪装提供真实文件名与符号名
```

以下几处的实现是被具体问题逼出来的，改动前值得先读：

**进度存字符偏移，不存段号。** 段合并阈值一改，段号的含义就变了，旧存档会漂到错误位置；
字符偏移对分页参数免疫。v1 的 `{"seg": N}` 在首次启动时经 `Book.raw_starts` 换算迁移。

**恢复键绝不能含 Esc。** 紧张时人会连拍 Esc；若双击 Esc 也能恢复，三次 200ms 内的 Esc
就等于「进去又出来」，人还没走到你身后书就回到屏幕上了。`Ctrl-L` 除了不在乱按路径上，
语义也顺 —— 在一个真的 watch 进程前按 Ctrl-L 清屏刷新完全自然。

**打字模式严格前进。** 只有敲对了游标才右移。最初的实现用 `while len(buf) < len(target)`
只数长度不看对错，连按 400 次空格就能「打」完一段并翻页，练打字的意义归零。配套两条：
错字渲染的仍是**原文该出现的字符**（标红），否则正文会被自己的手误改写；每帧渲染完整
目标只切颜色，ghost 宽度恒定，光标和折行不会漂。

**跳过判据是值域而不是白名单。** `term.read_key` 一次 `os.read` 只取一个字节，所以永远
只返回 ASCII —— `ord > 126` 的字符不可能匹配上，`ord < 32` 的控制字符被主循环当控制键
忽略，停在两者上面都是死锁。早期用手写字符表，漏掉了软连字符 U+00AD（Ball Lightning
一本有 7438 个，零宽不可见），表现成「打到某个词就卡死」。现在按 `32 <= ord(ch) <= 126`
判断，并在解析期就删掉零宽排版字符。

**epub 解析的三个坑。** 正文既在 `<p>` 也在 `<blockquote>`（三体英译本第 13 章有 89 个
blockquote / 7 个 p，只取 `<p>` 会整章丢失）；小于 1500 字节的 spine item 是脚注页/版权页
（实测 42 个），丢掉但 `toc.ncx` 落在其上的目录项要沿 spine 顺延，否则 Part 分隔会丢；
章节标题同时出现在 ncx 和正文里（实测 20 处），正文侧要滤掉，否则一次按键只出十几个字符。

**分段参数。** 目标 200 字符、硬上限 700，在句末标点附近 70 字符窗口内找切点。实测一本书
2822 个原始段落里 24.9% 短于 80 字符，最长有 25 段对白连击 —— 不合并的话半屏推进会退化
成一行一按。

## 开发

```bash
pip install -e .
python3 -m pytest          # 106 个测试
```

`tests/test_app.py` 和 `tests/test_term.py` 在真 pty 里跑，改动前先看模块 docstring ——
下面几条从现象反推不到原因：

- **读取必须与注入并行**（起线程）。渲染一屏几百字节，pty 输出缓冲填满后子进程阻塞在
  `write` 上、按键一个都不消费；先注入后读会让所有按键一次性到达，`read_key` 的 30ms
  Esc 判据全部失效。
- **不能先 sleep 再读** —— macOS 上最后一个 slave fd 关闭时会丢弃未读数据，早退子进程的
  输出会全没。
- **注入单个 Esc 后要留 > 30ms 间隔**，否则会被下一个按键的首字节接成转义序列 —— 那正是
  `read_key` 要区分的两种情况。
- **子进程 `HOME` 必须指向 tmp**（`state.PATH` 从 `Path.home()` 算），否则会覆盖真进度。
- **`pty.fork` 默认窗口 0x0**，要 `TIOCSWINSZ` 设真实宽度，不然 `term.cols` 退到下限 48。
- **主题超宽要扫一批 offset** —— 超宽来自 offset 派生的数字和选中的符号名长度，单个
  offset 测不出来。

`novel_data/` 在 `.gitignore` 里：epub 是版权内容，仓库是公开的。

## FAQ

**打到某个词就卡住，敲什么都没反应？**
早期版本的 bug，已修复。epub 用软连字符（U+00AD）标断行候选点，它渲染出来零宽、屏幕上
完全看不见，而打字模式严格前进 —— 光标停在这个不可见字符上，敲下一个看得见的字符不匹配，
游标就不动。现在解析期直接删掉零宽字符，打字期按值域跳过所有敲不出的字符。

**支持中文小说吗？**
阅读可以，但打字模式不支持（一次单字节读取只能匹配 ASCII），且正文归一化是按英文排版
设计的。

**每次启动都要重新解析 epub？**
是。三体英译本约 0.6 秒，换来没有需要维护的缓存或索引，也不会有「书更新了但缓存没刷」
这类问题。

**支持 mobi / azw3 / txt 吗？**
目前只支持 epub。

## License

[MIT](LICENSE) © 2026 chenjiangwen
