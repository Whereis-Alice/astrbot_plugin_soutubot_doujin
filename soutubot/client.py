"""soutubot.moe 的异步 HTTP 客户端。

站点没有公开 API 文档，本模块的调用方式来自对官方前端脚本
（`https://soutubot.moe/build/assets/app-*.js`）的分析，并经过真实请求验证：

- `POST /api/search`
  `multipart/form-data`，字段 `file`（图片二进制）+ `factor`（1.2 普通 / 1.4 严格）
- `GET /api/results/{id}`
  按结果 ID 复查同一次搜索

鉴权头 `X-API-KEY` 由前端本地计算，等价 Python 实现::

    raw = str(int(time.time()) ** 2 + len(user_agent) ** 2 + boot_token)
    key = base64.b64encode(raw.encode()).decode()[::-1].replace("=", "")

其中 `boot_token` 是首页 HTML 里 `window.GLOBAL.m` 的值，每次访问首页都会变化，
因此必须先抓首页再签名。签名同时绑定了本机时间和 UA 长度：
系统时间偏差过大或 UA 与签名时不一致，服务端会返回 401。
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
import uuid
from typing import Any

import aiohttp

from .models import SoutubotSearchResult

DEFAULT_BASE_URL = "https://soutubot.moe"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

FACTOR_NORMAL = 1.2
FACTOR_STRICT = 1.4

# window.GLOBAL = { siteConfig: ..., m: 1848392517136 }
_BOOT_TOKEN_PATTERN = re.compile(r"\bm\s*:\s*(\d{6,})")

# boot token 缓存时长（秒）。太长会增加 401 概率，太短会多打首页。
_BOOT_TOKEN_TTL = 120.0


class SoutubotError(Exception):
    """soutubot 调用相关错误的基类。"""


class SoutubotNetworkError(SoutubotError):
    """网络层错误（连接失败、超时、代理不可用等）。"""


class SoutubotAuthError(SoutubotError):
    """签名被拒绝（HTTP 401），通常是本机时间不准或 UA 不一致。"""


class SoutubotRateLimitError(SoutubotError):
    """被限流（HTTP 429）。"""


class SoutubotUpstreamError(SoutubotError):
    """上游其他异常状态码或响应无法解析。"""


def extract_boot_token(html: str) -> int:
    """从首页 HTML 中取出 `window.GLOBAL.m`。"""
    match = _BOOT_TOKEN_PATTERN.search(html or "")
    if not match:
        raise SoutubotUpstreamError("未能从 soutubot 首页解析出签名参数（站点结构可能已变更）")
    return int(match.group(1))


def build_api_key(boot_token: int, user_agent: str, now: float | None = None) -> str:
    """复刻前端的 `X-API-KEY` 计算方式。"""
    timestamp = int(now if now is not None else time.time())
    raw = str(timestamp**2 + len(user_agent) ** 2 + int(boot_token))
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")[::-1].replace("=", "")


class SoutubotClient:
    """线程/协程安全的 soutubot.moe 客户端。

    - 自动获取并缓存签名参数
    - 401 时自动刷新签名重试
    - 429 / 5xx 指数退避重试
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 60.0,
        proxy: str = "",
        max_retries: int = 2,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.timeout = max(5.0, float(timeout))
        self.proxy = proxy or None
        self.max_retries = max(0, int(max_retries))

        self._session = session
        self._owns_session = session is None
        self._boot_token: int | None = None
        self._boot_token_at = 0.0
        self._boot_lock = asyncio.Lock()

    # ---- 生命周期 ----

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    # ---- 签名 ----

    def _base_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": f"{self.base_url}/",
            "Origin": self.base_url,
            "X-Requested-With": "XMLHttpRequest",
        }

    async def _get_boot_token(self, force: bool = False) -> int:
        async with self._boot_lock:
            fresh = (
                self._boot_token is not None
                and (time.monotonic() - self._boot_token_at) < _BOOT_TOKEN_TTL
            )
            if fresh and not force:
                return self._boot_token  # type: ignore[return-value]

            session = await self._ensure_session()
            headers = {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
            try:
                async with session.get(
                    f"{self.base_url}/", headers=headers, proxy=self.proxy
                ) as response:
                    if response.status != 200:
                        raise SoutubotUpstreamError(
                            f"访问 soutubot 首页失败：HTTP {response.status}"
                        )
                    html = await response.text(errors="ignore")
            except aiohttp.ClientError as exc:
                raise SoutubotNetworkError(f"无法访问 soutubot：{exc}") from exc
            except asyncio.TimeoutError as exc:
                raise SoutubotNetworkError("访问 soutubot 首页超时") from exc

            self._boot_token = extract_boot_token(html)
            self._boot_token_at = time.monotonic()
            return self._boot_token

    async def _auth_headers(self, force_refresh: bool = False) -> dict[str, str]:
        boot_token = await self._get_boot_token(force=force_refresh)
        headers = self._base_headers()
        headers["X-API-KEY"] = build_api_key(boot_token, self.user_agent)
        return headers

    # ---- 请求 ----

    @staticmethod
    def _build_multipart(
        image: bytes, factor: float, filename: str, content_type: str
    ) -> tuple[bytes, str]:
        boundary = uuid.uuid4().hex
        parts: list[bytes] = []
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(image)
        parts.append(b"\r\n")
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="factor"\r\n\r\n{factor}\r\n'
                f"--{boundary}--\r\n"
            ).encode("utf-8")
        )
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str = "",
    ) -> dict[str, Any]:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        last_error: SoutubotError | None = None

        for attempt in range(self.max_retries + 1):
            headers = await self._auth_headers(force_refresh=attempt > 0)
            if content_type:
                headers["Content-Type"] = content_type

            try:
                async with session.request(
                    method, url, data=body, headers=headers, proxy=self.proxy
                ) as response:
                    status = response.status
                    text = await response.text(errors="ignore")
            except aiohttp.ClientError as exc:
                last_error = SoutubotNetworkError(f"请求 soutubot 失败：{exc}")
            except asyncio.TimeoutError:
                last_error = SoutubotNetworkError("请求 soutubot 超时")
            else:
                if status == 200:
                    return _parse_json(text)
                if status == 401:
                    last_error = SoutubotAuthError(
                        "soutubot 拒绝了签名（HTTP 401）。"
                        "常见原因：服务器系统时间不准，请校准 NTP 后重试。"
                    )
                elif status == 429:
                    last_error = SoutubotRateLimitError(
                        "soutubot 提示请求过于频繁（HTTP 429），请稍后再试。"
                    )
                elif status in {413, 415, 422}:
                    # 图片本身有问题，重试没有意义
                    raise SoutubotUpstreamError(
                        f"soutubot 不接受这张图片（HTTP {status}），请换一张或缩小尺寸。"
                    )
                elif 500 <= status < 600:
                    last_error = SoutubotUpstreamError(
                        f"soutubot 服务端异常（HTTP {status}），请稍后再试。"
                    )
                else:
                    raise SoutubotUpstreamError(f"soutubot 返回了意外状态码 HTTP {status}")

            if attempt < self.max_retries:
                await asyncio.sleep(min(4.0, 1.0 * (2**attempt)))

        raise last_error or SoutubotUpstreamError("soutubot 请求失败")

    # ---- 对外 API ----

    async def search(
        self,
        image: bytes,
        *,
        factor: float = FACTOR_NORMAL,
        filename: str = "blob.jpg",
        content_type: str = "image/jpeg",
    ) -> SoutubotSearchResult:
        """以图搜本子。`factor` 越大越严格（前端只用 1.2 / 1.4）。"""
        if not image:
            raise SoutubotError("图片内容为空，无法搜索")
        body, ctype = self._build_multipart(image, factor, filename, content_type)
        payload = await self._request_json(
            "POST", "/api/search", body=body, content_type=ctype
        )
        return SoutubotSearchResult.from_api(payload)

    async def fetch_result(self, result_id: str) -> SoutubotSearchResult:
        """按结果 ID 重新拉取一次搜索结果。"""
        result_id = str(result_id or "").strip()
        if not result_id.isdigit():
            raise SoutubotError("结果 ID 不合法")
        payload = await self._request_json("GET", f"/api/results/{result_id}")
        return SoutubotSearchResult.from_api(payload)


def _parse_json(text: str) -> dict[str, Any]:
    import json

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SoutubotUpstreamError("soutubot 返回了无法解析的内容") from exc
    if not isinstance(payload, dict):
        raise SoutubotUpstreamError("soutubot 返回结构异常")
    return payload
