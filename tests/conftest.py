"""共享 fixture。真 epub 存在就用它，否则用合成的 —— 测试不能依赖版权文件。"""
import zipfile
from pathlib import Path

import pytest

REAL = sorted((Path(__file__).resolve().parents[1] / "novel_data").glob("*.epub"))

CONTAINER = b"""<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
 <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>
</container>"""


def _opf(items, spine):
    man = "".join(f'<item id="{i}" href="{h}" media-type="{m}"/>'
                  for i, h, m in items)
    ref = "".join(f'<itemref idref="{i}"/>' for i in spine)
    return (f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf">'
            f"<manifest>{man}</manifest><spine toc=\"ncx\">{ref}</spine></package>"
            ).encode()


def _ncx(points):
    ps = "".join(
        f'<navPoint><navLabel><text>{t}</text></navLabel>'
        f'<content src="{s}"/></navPoint>' for t, s in points)
    return (f'<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
            f"<navMap>{ps}</navMap></ncx>").encode()


def _page(paras):
    body = "".join(f"<p>{t}</p>" for t in paras)
    pad = "<!--" + "x" * 2000 + "-->"        # 撑过 MIN_FILE，否则整页被当脚注页丢掉
    return f"<html><body>{body}</body>{pad}</html>".encode()


@pytest.fixture
def synth_epub(tmp_path):
    """两章 + 一个被过滤的小文件（章节标签必须顺延到下一页）。"""
    p = tmp_path / "synth.epub"
    long1 = "Chapter one prose. " * 12
    long2 = "Chapter two prose, rather longer than the first one here. " * 9
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", _opf(
            [("ncx", "toc.ncx", "application/x-dtbncx+xml"),
             ("t1", "part.xhtml", "application/xhtml+xml"),
             ("c1", "c1.xhtml", "application/xhtml+xml"),
             ("c2", "c2.xhtml", "application/xhtml+xml")],
            ["t1", "c1", "c2"]))
        z.writestr("OEBPS/toc.ncx", _ncx([("Part I: Beginnings", "part.xhtml"),
                                          ("1. First Chapter", "c1.xhtml"),
                                          ("2. Second Chapter", "c2.xhtml")]))
        z.writestr("OEBPS/part.xhtml", b"<html><body><p>Part I</p></body></html>")
        z.writestr("OEBPS/c1.xhtml", _page(["1. First Chapter", long1,
                                            "Short one here, still long enough."]))
        z.writestr("OEBPS/c2.xhtml", _page(["2. Second Chapter", long2,
                                            "Tail paragraph of the second chapter."]))
    return str(p)


@pytest.fixture
def real_book():
    if not REAL:
        pytest.skip("no epub in novel_data/")
    from noveltyper import book
    return book.load(str(REAL[0]))
