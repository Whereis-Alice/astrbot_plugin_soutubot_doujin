"""utils.py：配置读取、magic bytes 识别、图片预处理与本地/远程取图。"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import pytest

from soutubot.utils import (
    DEFAULT_DOWNLOAD_LIMIT,
    DEFAULT_MAX_WIDTH,
    PASSTHROUGH_MIME,
    ImageTooLargeError,
    fetch_image_bytes,
    local_path_from_reference,
    prepare_image,
    read_bool,
    read_float,
    read_int,
    read_list,
    read_str,
    sha256_hex,
    sniff_mime,
)

try:
    from PIL import Image as PILImage
except Exception:  # pragma: no cover - 环境相关
    PILImage = None

requires_pillow = pytest.mark.skipif(
    PILImage is None, reason="需要 Pillow 才能验证图片预处理"
)


# ---------------------------------------------------------------- sha256


def test_sha256_hex_known_values():
    assert sha256_hex(b"") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert sha256_hex(b"soutubot") == hashlib.sha256(b"soutubot").hexdigest()
    assert len(sha256_hex(b"\x00\xff")) == 64


def test_sha256_hex_is_stable_and_distinct():
    assert sha256_hex(b"a") == sha256_hex(b"a")
    assert sha256_hex(b"a") != sha256_hex(b"b")


# ---------------------------------------------------------------- read_bool


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        (None, True),  # None -> default
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("  Yes  ", True),
        ("on", True),
        ("enabled", True),
        ("开", True),
        ("是", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("disabled", False),
        ("关", False),
        ("否", False),
        ("maybe", True),  # 无法识别 -> default
        ("", True),
        (1, True),
        (0, False),
        ([], False),
        ([1], True),
    ],
)
def test_read_bool_with_default_true(value, expected):
    assert read_bool(value, True) is expected


def test_read_bool_unknown_uses_given_default():
    assert read_bool("maybe", False) is False
    assert read_bool(None, False) is False
    assert read_bool("", False) is False
    # 明确的取值不受 default 影响
    assert read_bool("off", True) is False
    assert read_bool("on", False) is True


# ---------------------------------------------------------------- read_int


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5, 5),
        ("42", 42),
        ("3.7", 3),  # 先 float 再截断
        (3.9, 3),
        (-2, -2),
        ("-8", -8),
        (None, 7),
        ("abc", 7),
        ("", 7),
        (True, 7),  # bool 不当数字
        (False, 7),
        ([], 7),
    ],
)
def test_read_int_parsing(value, expected):
    assert read_int(value, 7) == expected


def test_read_int_clamps_to_range():
    assert read_int(100, 5, minimum=1, maximum=10) == 10
    assert read_int(-100, 5, minimum=1, maximum=10) == 1
    assert read_int(5, 5, minimum=1, maximum=10) == 5
    assert read_int("abc", 50, minimum=1, maximum=10) == 10  # default 也被钳制
    assert read_int(3, 5, minimum=4) == 4
    assert read_int(30, 5, maximum=20) == 20


# ---------------------------------------------------------------- read_float


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.5, 1.5),
        ("2.25", 2.25),
        (3, 3.0),
        ("-0.5", -0.5),
        (None, 9.5),
        ("abc", 9.5),
        (True, 9.5),
        ({}, 9.5),
    ],
)
def test_read_float_parsing(value, expected):
    assert read_float(value, 9.5) == pytest.approx(expected)


def test_read_float_clamps_to_range():
    assert read_float(99.0, 1.0, minimum=0.5, maximum=5.0) == pytest.approx(5.0)
    assert read_float(0.1, 1.0, minimum=0.5, maximum=5.0) == pytest.approx(0.5)
    assert read_float("bad", 1.0, minimum=2.0) == pytest.approx(2.0)


# ---------------------------------------------------------------- read_str


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("x", "x"),
        ("  padded  ", "padded"),
        ("", "fallback"),
        ("   ", "fallback"),
        (None, "fallback"),
        (0, "0"),
        (12, "12"),
    ],
)
def test_read_str(value, expected):
    assert read_str(value, "fallback") == expected


def test_read_str_default_is_empty_string():
    assert read_str(None) == ""
    assert read_str("  ") == ""


# ---------------------------------------------------------------- read_list


def test_read_list_splits_ascii_comma():
    assert read_list("a,b,c") == ["a", "b", "c"]
    assert read_list(" a , b ,, c ") == ["a", "b", "c"]  # 空项被丢掉


def test_read_list_splits_fullwidth_comma():
    assert read_list("甲，乙，丙") == ["甲", "乙", "丙"]
    assert read_list("甲，乙,丙") == ["甲", "乙", "丙"]  # 中英文逗号混用


def test_read_list_splits_newlines():
    assert read_list("a\nb\nc") == ["a", "b", "c"]
    assert read_list("a\r\nb\r\nc") == ["a", "b", "c"]


def test_read_list_dedupes_preserving_order():
    assert read_list("b,a,b,c,a") == ["b", "a", "c"]


def test_read_list_from_sequences():
    assert read_list(["  a ", "a", "b"]) == ["a", "b"]
    assert read_list(("x", "y")) == ["x", "y"]
    assert read_list([1, 2, 2]) == ["1", "2"]


def test_read_list_defaults():
    assert read_list(None) == []
    assert read_list(None, ["d"]) == ["d"]
    assert read_list("", ["d"]) == ["d"]
    assert read_list("  ,  ", ["d"]) == ["d"]  # 全空 -> default
    assert read_list([], ["d"]) == ["d"]
    assert read_list(42, ["d"]) == ["d"]  # 不支持的类型 -> default


def test_read_list_does_not_share_default_instance():
    default = ["a"]
    result = read_list(None, default)
    result.append("b")
    assert default == ["a"]


# ---------------------------------------------------------------- sniff_mime


def pad(prefix: bytes, size: int = 32) -> bytes:
    return prefix + b"\x00" * max(0, size - len(prefix))


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (pad(b"\xff\xd8\xff\xe0"), "image/jpeg"),
        (pad(b"\xff\xd8\xff\xdb"), "image/jpeg"),
        (pad(b"\x89PNG\r\n\x1a\n"), "image/png"),
        (b"RIFF" + b"\x24\x00\x00\x00" + b"WEBPVP8 " + b"\x00" * 8, "image/webp"),
        (pad(b"GIF87a"), "image/gif"),
        (pad(b"GIF89a"), "image/gif"),
        (pad(b"II*\x00"), "image/tiff"),
        (pad(b"MM\x00*"), "image/tiff"),
        (b"\x00\x00\x00\x20" + b"ftyp" + b"heic" + b"\x00" * 8, "image/heic"),
        (b"\x00\x00\x00\x20" + b"ftyp" + b"mif1" + b"\x00" * 8, "image/heic"),
    ],
)
def test_sniff_mime_magic_bytes(data, expected):
    assert sniff_mime(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"short",
        b"\xff\xd8\xff",  # 不足 12 字节，即使前缀正确也返回空
        pad(b"NOTANIMAGE"),
        pad(b"RIFF____WAVE"),  # RIFF 但不是 WEBP
    ],
)
def test_sniff_mime_unknown(data):
    assert sniff_mime(data) == ""


def test_sniff_mime_webp_requires_webp_tag():
    assert sniff_mime(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8) == "image/webp"
    assert sniff_mime(b"RIFF" + b"\x00" * 4 + b"AVI " + b"\x00" * 8) == ""


def test_sniff_mime_detects_bmp():
    header = b"BM" + (100).to_bytes(4, "little") + b"\x00" * 26
    assert sniff_mime(header) == "image/bmp"


def test_passthrough_mime_set():
    assert PASSTHROUGH_MIME == {"image/jpeg", "image/png", "image/webp"}
    assert DEFAULT_MAX_WIDTH == 2000


# ---------------------------------------------------------------- prepare_image


def make_png(width: int, height: int, color=(200, 120, 60)) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    PILImage.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_prepare_image_rejects_empty():
    with pytest.raises(ValueError):
        prepare_image(b"")


@requires_pillow
def test_prepare_image_passes_small_png_through():
    data = make_png(64, 48)
    content, filename, ctype = prepare_image(data)
    assert content == data  # 原样透传，不重新编码
    assert filename == "blob.png"
    assert ctype == "image/png"


@requires_pillow
def test_prepare_image_resizes_wide_image_to_jpeg():
    from io import BytesIO

    data = make_png(2500, 100)
    content, filename, ctype = prepare_image(data, max_width=2000)
    assert filename == "blob.jpg"
    assert ctype == "image/jpeg"
    assert content != data
    assert sniff_mime(content) == "image/jpeg"
    with PILImage.open(BytesIO(content)) as image:
        assert image.size == (2000, 80)  # 等比缩放
        assert image.format == "JPEG"


@requires_pillow
def test_prepare_image_custom_max_width():
    from io import BytesIO

    content, _, ctype = prepare_image(make_png(800, 400), max_width=400)
    assert ctype == "image/jpeg"
    with PILImage.open(BytesIO(content)) as image:
        assert image.size == (400, 200)


@requires_pillow
def test_prepare_image_converts_non_passthrough_format():
    from io import BytesIO

    buffer = BytesIO()
    PILImage.new("P", (40, 40)).save(buffer, format="GIF")
    gif = buffer.getvalue()
    assert sniff_mime(gif) == "image/gif"

    content, filename, ctype = prepare_image(gif)
    assert (filename, ctype) == ("blob.jpg", "image/jpeg")
    assert sniff_mime(content) == "image/jpeg"


@requires_pillow
def test_prepare_image_recompresses_when_over_size_budget():
    data = make_png(64, 64)
    content, filename, ctype = prepare_image(data, recompress_over_bytes=1)
    assert (filename, ctype) == ("blob.jpg", "image/jpeg")
    assert content != data


@requires_pillow
def test_prepare_image_flattens_transparency():
    from io import BytesIO

    buffer = BytesIO()
    PILImage.new("RGBA", (30, 30), (255, 0, 0, 0)).save(buffer, format="PNG")
    content, _, ctype = prepare_image(buffer.getvalue(), recompress_over_bytes=1)
    assert ctype == "image/jpeg"
    with PILImage.open(BytesIO(content)) as image:
        assert image.mode == "RGB"


def test_prepare_image_falls_back_on_corrupt_data():
    corrupt = b"\x89PNG\r\n\x1a\n" + b"not really a png" * 4
    content, filename, ctype = prepare_image(corrupt)
    assert content == corrupt  # 解析失败时安全回退
    assert filename == "blob.png"
    assert ctype == "image/png"


def test_prepare_image_unknown_format_fallback():
    blob = b"\x00" * 64
    content, filename, ctype = prepare_image(blob)
    assert content == blob
    assert filename == "blob.jpg"
    assert ctype == "image/jpeg"


# ---------------------------------------------------------------- 本地路径


def test_local_path_from_reference_plain_path(tmp_path: Path):
    target = tmp_path / "pic.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    assert local_path_from_reference(str(target)) == str(target)


def test_local_path_from_reference_strips_quotes(tmp_path: Path):
    target = tmp_path / "pic.png"
    target.write_bytes(b"x")
    assert local_path_from_reference(f'"{target}"') == str(target)
    assert local_path_from_reference(f"'{target}'") == str(target)
    assert local_path_from_reference(f"  {target}  ") == str(target)


def test_local_path_from_reference_file_uri(tmp_path: Path):
    target = tmp_path / "with space.png"
    target.write_bytes(b"x")
    uri = target.as_uri()  # Windows 下形如 file:///C:/...
    assert "%20" in uri
    resolved = local_path_from_reference(uri)
    assert os.path.isfile(resolved)
    assert os.path.normpath(resolved) == os.path.normpath(str(target))


@pytest.mark.skipif(os.name != "nt", reason="Windows 专属的 file:///C:/ 解析")
def test_local_path_from_reference_windows_drive_uri(tmp_path: Path):
    target = tmp_path / "drive.png"
    target.write_bytes(b"x")
    uri = "file:///" + str(target).replace("\\", "/").replace(" ", "%20")
    assert uri.startswith("file:///") and uri[9] == ":"
    resolved = local_path_from_reference(uri)
    # 前导斜杠必须被去掉，否则 os.path.isfile 会失败
    assert resolved[1] == ":"
    assert os.path.normpath(resolved) == os.path.normpath(str(target))


def test_local_path_from_reference_missing_paths(tmp_path: Path):
    missing = tmp_path / "nope.png"
    assert local_path_from_reference(str(missing)) == ""
    assert local_path_from_reference(missing.as_uri()) == ""
    assert local_path_from_reference("") == ""
    assert local_path_from_reference("   ") == ""
    assert local_path_from_reference(None) == ""
    assert local_path_from_reference("https://example.com/a.png") == ""
    assert local_path_from_reference(str(tmp_path)) == ""  # 目录不算文件


# ---------------------------------------------------------------- fetch_image


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def iter_chunked(self, size):
        for chunk in self._chunks:
            yield chunk


class _FakeHttpResponse:
    def __init__(self, status=200, chunks=(b"data",), headers=None):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeHttpSession:
    """只实现 fetch_image_bytes 需要的 get()，不做任何真实网络访问。"""

    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self._response


def run(coro):
    return asyncio.run(coro)


def test_fetch_image_bytes_reads_local_file(tmp_path: Path):
    target = tmp_path / "local.png"
    target.write_bytes(b"\x89PNG payload")
    data = run(fetch_image_bytes(None, str(target)))
    assert data == b"\x89PNG payload"


def test_fetch_image_bytes_local_file_uri(tmp_path: Path):
    target = tmp_path / "local2.png"
    target.write_bytes(b"abc")
    assert run(fetch_image_bytes(None, target.as_uri())) == b"abc"


def test_fetch_image_bytes_local_too_large(tmp_path: Path):
    target = tmp_path / "big.png"
    target.write_bytes(b"0" * 1024)
    with pytest.raises(ImageTooLargeError):
        run(fetch_image_bytes(None, str(target), max_bytes=100))


def test_fetch_image_bytes_rejects_empty_and_unknown_scheme():
    with pytest.raises(ValueError):
        run(fetch_image_bytes(None, ""))
    with pytest.raises(ValueError):
        run(fetch_image_bytes(None, "ftp://example.com/a.png"))
    with pytest.raises(ValueError):
        run(fetch_image_bytes(None, "not a path at all"))


def test_fetch_image_bytes_http_success():
    session = _FakeHttpSession(_FakeHttpResponse(chunks=[b"ab", b"cd"]))
    data = run(
        fetch_image_bytes(
            session, "https://example.com/a.png", proxy="http://127.0.0.1:1080"
        )
    )
    assert data == b"abcd"
    assert session.calls[0]["url"] == "https://example.com/a.png"
    assert session.calls[0]["proxy"] == "http://127.0.0.1:1080"
    assert session.calls[0]["allow_redirects"] is True


def test_fetch_image_bytes_http_error_status():
    session = _FakeHttpSession(_FakeHttpResponse(status=404))
    with pytest.raises(ValueError):
        run(fetch_image_bytes(session, "https://example.com/a.png"))


def test_fetch_image_bytes_rejects_declared_content_length():
    session = _FakeHttpSession(
        _FakeHttpResponse(headers={"Content-Length": str(10**9)})
    )
    with pytest.raises(ImageTooLargeError):
        run(fetch_image_bytes(session, "https://example.com/a.png", max_bytes=1024))


def test_fetch_image_bytes_rejects_streamed_overflow():
    session = _FakeHttpSession(_FakeHttpResponse(chunks=[b"x" * 60, b"x" * 60]))
    with pytest.raises(ImageTooLargeError):
        run(fetch_image_bytes(session, "https://example.com/a.png", max_bytes=100))


def test_fetch_image_bytes_ignores_limits_when_zero():
    session = _FakeHttpSession(
        _FakeHttpResponse(chunks=[b"y" * 50], headers={"Content-Length": "50"})
    )
    assert len(run(fetch_image_bytes(session, "https://x/a.png", max_bytes=0))) == 50


def test_download_limit_constant():
    assert DEFAULT_DOWNLOAD_LIMIT == 32 * 1024 * 1024
