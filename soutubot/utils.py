"""图片预处理、下载与配置读取的通用工具。

图片预处理沿用 soutubot.moe 前端的策略：
- 只有 jpeg / png / webp 会被原样上传
- 其他格式、超宽（> 2000px）或体积过大的图片会被压成 JPEG（quality 90）

Pillow 缺失时不会报错，而是原样上传，把兼容性判断交给服务端。
"""

from __future__ import annotations

import hashlib
import os
import urllib.parse
from typing import Any

# 服务端接受的原生格式（其余一律转 JPEG）
PASSTHROUGH_MIME = {"image/jpeg", "image/png", "image/webp"}

DEFAULT_MAX_WIDTH = 2000
DEFAULT_JPEG_QUALITY = 90
# 超过该体积就重新编码，避免把十几 MB 的原图直接推给上游
DEFAULT_RECOMPRESS_BYTES = 4 * 1024 * 1024
# 下载时的硬上限，防止有人拿超大文件打满内存
DEFAULT_DOWNLOAD_LIMIT = 32 * 1024 * 1024


class ImageTooLargeError(Exception):
    """下载的图片超过允许的体积上限。"""


def sha256_hex(data: bytes) -> str:
    """返回字节内容的 sha256 十六进制摘要（用于结果缓存键）。"""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- 配置读取


def read_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "enabled", "开", "是"}:
            return True
        if lowered in {"0", "false", "no", "off", "disabled", "关", "否"}:
            return False
        return default
    return bool(value)


def read_int(
    value: Any,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        if isinstance(value, bool):
            raise TypeError
        result = int(float(value))
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def read_float(
    value: Any,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        if isinstance(value, bool):
            raise TypeError
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def read_str(value: Any, default: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def read_list(value: Any, default: list[str] | None = None) -> list[str]:
    """把配置值归一化为去空白、去重（保序）的字符串列表。"""
    default = list(default or [])
    if value is None:
        return default

    if isinstance(value, str):
        raw = [chunk for chunk in value.replace("\r", "\n").split("\n")]
        if len(raw) == 1:
            raw = raw[0].replace("，", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        return default

    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        text = item.strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result or default


# ---------------------------------------------------------------- 图片识别


def sniff_mime(data: bytes) -> str:
    """通过 magic bytes 猜测图片 MIME，未知返回空串。"""
    if len(data) < 12:
        return ""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"mif1"):
        return "image/heic"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    return ""


_EXTENSION_BY_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/tiff": "tiff",
    "image/heic": "heic",
}


def prepare_image(
    data: bytes,
    *,
    max_width: int = DEFAULT_MAX_WIDTH,
    quality: int = DEFAULT_JPEG_QUALITY,
    recompress_over_bytes: int = DEFAULT_RECOMPRESS_BYTES,
) -> tuple[bytes, str, str]:
    """把任意图片规整为上游能接受的形式。

    返回 ``(内容, 文件名, content_type)``。失败时安全回退为原始字节。
    """
    if not data:
        raise ValueError("图片内容为空")

    mime = sniff_mime(data)
    oversize = len(data) > max(0, recompress_over_bytes)
    needs_convert = mime not in PASSTHROUGH_MIME

    try:
        from io import BytesIO

        from PIL import Image as PILImage
    except Exception:
        # 没有 Pillow：原样上传，让服务端决定
        ext = _EXTENSION_BY_MIME.get(mime, "jpg")
        return data, f"blob.{ext}", mime or "image/jpeg"

    try:
        with PILImage.open(BytesIO(data)) as image:
            image.load()
            width, height = image.size
            too_wide = max_width > 0 and width > max_width

            if not (needs_convert or oversize or too_wide):
                ext = _EXTENSION_BY_MIME.get(mime, "jpg")
                return data, f"blob.{ext}", mime

            if too_wide:
                new_height = max(1, round(height * max_width / width))
                image = image.resize((max_width, new_height), PILImage.LANCZOS)

            if image.mode not in ("RGB", "L"):
                background = PILImage.new("RGB", image.size, (255, 255, 255))
                if image.mode in ("RGBA", "LA", "P"):
                    converted = image.convert("RGBA")
                    background.paste(converted, mask=converted.split()[-1])
                    image = background
                else:
                    image = image.convert("RGB")

            buffer = BytesIO()
            image.save(
                buffer,
                format="JPEG",
                quality=max(40, min(95, int(quality))),
                optimize=True,
            )
            return buffer.getvalue(), "blob.jpg", "image/jpeg"
    except Exception:
        ext = _EXTENSION_BY_MIME.get(mime, "jpg")
        return data, f"blob.{ext}", mime or "image/jpeg"


# ---------------------------------------------------------------- 图片获取


def local_path_from_reference(reference: str) -> str:
    """把本地路径或 ``file://`` URI 归一化为真实文件路径，否则返回空串。"""
    ref = (reference or "").strip().strip("`'\"")
    if not ref:
        return ""

    if ref.lower().startswith("file://"):
        parsed = urllib.parse.urlparse(ref)
        path = urllib.parse.unquote(parsed.path or "")
        if parsed.netloc:
            # file://server/share/x -> UNC
            path = f"//{parsed.netloc}{path}"
        elif os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":
            # Windows 下 file:///C:/x 会解析出前导斜杠
            path = path[1:]
        return path if os.path.isfile(path) else ""

    return ref if os.path.isfile(ref) else ""


async def fetch_image_bytes(
    session: Any,
    reference: str,
    *,
    proxy: str = "",
    timeout: float = 30.0,
    max_bytes: int = DEFAULT_DOWNLOAD_LIMIT,
) -> bytes:
    """读取图片内容，支持本地路径、``file://`` 与 ``http(s)://``。"""
    ref = (reference or "").strip().strip("`'\"")
    if not ref:
        raise ValueError("图片来源为空")

    local = local_path_from_reference(ref)
    if local:
        size = os.path.getsize(local)
        if max_bytes > 0 and size > max_bytes:
            raise ImageTooLargeError(f"图片体积 {size} 字节超过上限 {max_bytes} 字节")
        with open(local, "rb") as handle:
            return handle.read()

    if not ref.lower().startswith(("http://", "https://")):
        raise ValueError(f"无法识别的图片来源：{ref[:120]}")

    import aiohttp

    request_timeout = aiohttp.ClientTimeout(total=max(5.0, float(timeout)))
    async with session.get(
        ref,
        proxy=proxy or None,
        timeout=request_timeout,
        allow_redirects=True,
    ) as response:
        if response.status != 200:
            raise ValueError(f"下载图片失败（HTTP {response.status}）")

        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and max_bytes > 0:
            if int(declared) > max_bytes:
                raise ImageTooLargeError(
                    f"图片体积 {declared} 字节超过上限 {max_bytes} 字节"
                )

        chunks: list[bytes] = []
        total = 0
        async for chunk in response.content.iter_chunked(64 * 1024):
            total += len(chunk)
            if max_bytes > 0 and total > max_bytes:
                raise ImageTooLargeError(f"图片体积超过上限 {max_bytes} 字节")
            chunks.append(chunk)
        return b"".join(chunks)
