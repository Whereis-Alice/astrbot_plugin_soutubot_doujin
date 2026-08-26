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

站点整体位于 Cloudflare 之后。机房 / VPS 出口 IP 经常被 Cloudflare 拦下，
表现为 **HTTP 403**（注意：不是 401，和签名、系统时间都无关）。为此本模块：

1. 把失败响应解析成可读诊断（`CF-RAY` / `cf-mitigated` / 正文摘要），
   便于在日志里区分「浏览器挑战」「WAF 拦截」「地区限制」「站点自身拒绝」。
2. 支持可选的 curl_cffi 传输层，用真实 Chrome 的 TLS / HTTP2 指纹发请求，
   绕过基于指纹的机器人识别。`auto` 模式下只在遇到 403 时才自动切换。
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import inspect
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


# --------------------------------------------------------------- TLS 指纹伪装

IMPERSONATE_OFF = "off"
IMPERSONATE_AUTO = "auto"
IMPERSONATE_ON = "on"
DEFAULT_IMPERSONATE_TARGET = "chrome"

#: `tls_impersonate` 的三个语义化取值；其余值当作 curl_cffi 的目标名原样透传
IMPERSONATE_MODES = (IMPERSONATE_OFF, IMPERSONATE_AUTO, IMPERSONATE_ON)


def curl_cffi_available() -> bool:
    """当前环境是否装了 curl_cffi（可选依赖）。"""
    try:
        return importlib.util.find_spec("curl_cffi") is not None
    except (ImportError, ValueError):  # pragma: no cover - 极端环境
        return False


# ------------------------------------------------------------------- 异常


class SoutubotError(Exception):
    """soutubot 调用相关错误的基类。"""


class SoutubotNetworkError(SoutubotError):
    """网络层错误（连接失败、超时、代理不可用等）。"""


class SoutubotAuthError(SoutubotError):
    """签名被拒绝（HTTP 401），通常是本机时间不准或 UA 不一致。"""


class SoutubotRateLimitError(SoutubotError):
    """被限流（HTTP 429）。"""


class SoutubotBlockedError(SoutubotError):
    """被 Cloudflare 或站点拦截（HTTP 403）。

    与 401 不同：403 表示请求没能进到应用层，和签名、系统时间无关，
    绝大多数情况是出口 IP 的信誉或 TLS 指纹问题。
    """

    def __init__(
        self, message: str, *, kind: str = "unknown", detail: dict | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.detail = dict(detail or {})


class SoutubotUpstreamError(SoutubotError):
    """上游其他异常状态码或响应无法解析。"""


# --------------------------------------------------------------- 失败响应诊断

_DIAG_HEADERS = (
    "cf-ray",
    "cf-mitigated",
    "cf-cache-status",
    "server",
    "retry-after",
    "content-type",
)

_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def summarize_body(body: str, limit: int = 200) -> str:
    """把 HTML 错误页压成一行可读摘要，便于写进日志。"""
    text = _SCRIPT_RE.sub(" ", body or "")
    text = _ANY_TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if limit > 0 and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def collect_diagnostics(
    status: int, headers: Any = None, body: str = ""
) -> dict[str, Any]:
    """从失败响应里抽出定位问题真正需要的字段。"""
    detail: dict[str, Any] = {"status": int(status)}
    try:
        pairs = dict(headers or {})
    except (TypeError, ValueError):
        pairs = {}
    lowered = {str(k).lower(): str(v) for k, v in pairs.items()}
    for name in _DIAG_HEADERS:
        if lowered.get(name):
            detail[name] = lowered[name]
    snippet = summarize_body(body)
    if snippet:
        detail["body"] = snippet
    return detail


def format_diagnostics(detail: dict[str, Any]) -> str:
    """把诊断字典渲染成一行 `key=value` 文本。"""
    return " ".join(f"{key}={value!r}" for key, value in (detail or {}).items())


def classify_forbidden(headers: Any = None, body: str = "") -> str:
    """判断 403 的性质。

    返回 `challenge` / `waf` / `geo` / `cloudflare` / `origin`。
    """
    try:
        pairs = dict(headers or {})
    except (TypeError, ValueError):
        pairs = {}
    lowered = {str(k).lower(): str(v).lower() for k, v in pairs.items()}
    text = (body or "").lower()

    if "challenge" in lowered.get("cf-mitigated", ""):
        return "challenge"
    if any(
        marker in text
        for marker in (
            "just a moment",
            "cf-browser-verification",
            "challenge-platform",
            "cf_chl_opt",
            "enable javascript and cookies to continue",
        )
    ):
        return "challenge"
    if any(
        marker in text
        for marker in (
            "you have been blocked",
            "attention required",
            "cloudflare to restrict access",
            "error 1020",
            "access denied",
        )
    ):
        return "waf"
    if any(
        marker in text
        for marker in ("not available in your country", "region is not supported")
    ):
        return "geo"
    if "cloudflare" in lowered.get("server", "") or lowered.get("cf-ray"):
        return "cloudflare"
    return "origin"


_FORBIDDEN_ADVICE = {
    "challenge": (
        "Cloudflare 要求浏览器验证（JS 挑战）。"
        "先把配置项 tls_impersonate 设为 auto 或 on（需要 pip install curl_cffi）；"
        "仍然被拦说明该出口 IP 信誉过低，请换网络或换代理。"
    ),
    "waf": (
        "Cloudflare WAF 直接拦掉了这个来源 IP。"
        "机房 / VPS / 公共代理 IP 很常见：换一个干净的出口，"
        "或按 README 部署 Cloudflare Worker 反向代理后填入 reverse_proxy_url。"
    ),
    "geo": "站点或 Cloudflare 屏蔽了当前地区，请通过代理访问。",
    "cloudflare": (
        "被 Cloudflare 拒绝。先试着把 tls_impersonate 设为 auto 或 on；"
        "无效则更换出口 IP、配置代理，"
        "或按 README 部署 Cloudflare Worker 反向代理（reverse_proxy_url）。"
    ),
    "origin": "站点主动拒绝了这次请求，可能是接口结构或防护策略变了。",
}


#: 已经在走反向代理时，建议要换一套说法：本机指纹此时对 soutubot 不可见
_PROXY_FORBIDDEN_ADVICE = (
    "请求是经反向代理发出的，403 来自代理本身或代理的上游。请依次检查："
    "① 反代地址填写正确且已部署成功；"
    "② 若设置了访问口令，插件里的 reverse_proxy_token 必须与反代一致；"
    "③ 反代必须把 soutubot 原始的 User-Agent 透传出去，否则签名会被判无效；"
    "④ 反代所在节点自身可能也被拦，可换个部署区域或改回直连。"
)


def forbidden_message(kind: str, detail: dict[str, Any], *, where: str) -> str:
    """组装 403 的中文错误文案，带上 CF-RAY 方便排查与反馈。"""
    info = detail or {}
    if info.get("via") == "reverse_proxy":
        advice = _PROXY_FORBIDDEN_ADVICE
    else:
        advice = _FORBIDDEN_ADVICE.get(kind, _FORBIDDEN_ADVICE["cloudflare"])
    ray = info.get("cf-ray")
    tail = f"（CF-RAY {ray}）" if ray else ""
    return f"{where}被拒绝：HTTP 403{tail}。{advice}"


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


class _HttpResponse:
    """两种传输层的统一响应形态。"""

    __slots__ = ("status", "headers", "text")

    def __init__(self, status: int, headers: Any, text: str) -> None:
        self.status = int(status)
        self.headers = headers if headers is not None else {}
        self.text = text or ""


# ------------------------------------------------------------- 反向代理（自建/CF）

def normalize_reverse_proxy(url: str) -> str:
    """把用户填写的反代地址归一化成 `https://host[/path]`（无尾斜杠）。

    容错处理：省略协议时补 `https://`；填了非 http(s) 协议则视为无效，返回空串
    （等价于关闭反代），避免一处填错就让整个插件不可用。
    """
    text = str(url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    if not text.startswith(("http://", "https://")):
        return ""
    return text.rstrip("/")


class SoutubotClient:
    """线程/协程安全的 soutubot.moe 客户端。

    - 自动获取并缓存签名参数，401 时刷新签名重试
    - 429 / 5xx 指数退避重试
    - 403（Cloudflare 拦截）单独分类并给出可执行建议
    - 可选走反向代理（Cloudflare Worker / 自建 Nginx）
    - 可选用 curl_cffi 伪装真实浏览器 TLS 指纹
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
        impersonate: str = IMPERSONATE_AUTO,
        cookie: str = "",
        reverse_proxy: str = "",
        reverse_proxy_token: str = "",
        curl_session: Any = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.timeout = max(5.0, float(timeout))
        self.proxy = proxy or None
        self.max_retries = max(0, int(max_retries))
        self.cookie = str(cookie or "").strip()

        # 反向代理：请求打到反代，但 Referer / Origin 仍然是 soutubot 本身
        self.reverse_proxy = normalize_reverse_proxy(reverse_proxy)
        self.reverse_proxy_token = str(reverse_proxy_token or "").strip()

        # TLS 指纹伪装
        mode = str(impersonate or IMPERSONATE_AUTO).strip().lower() or IMPERSONATE_AUTO
        self.impersonate_mode = mode
        self.impersonate_target = (
            DEFAULT_IMPERSONATE_TARGET
            if mode in IMPERSONATE_MODES
            else mode
        )
        self._curl_session = curl_session
        self._owns_curl_session = curl_session is None
        self._curl_active = mode != IMPERSONATE_OFF and mode != IMPERSONATE_AUTO
        if self._curl_active and curl_session is None and not curl_cffi_available():
            self._curl_active = False

        #: 最近一次失败响应的诊断信息，便于命令层打日志
        self.last_failure: dict[str, Any] | None = None

        self._session = session
        self._owns_session = session is None
        # 调用方自带 session 时（例如单元测试）视为它自己管传输层，不做自动升级
        self._transport_managed = session is None
        self._boot_token: int | None = None
        self._boot_token_at = 0.0
        self._boot_lock = asyncio.Lock()

    # ---- 传输层状态 ----

    @property
    def transport(self) -> str:
        """当前实际使用的 HTTP 传输层。"""
        if self._curl_active:
            return f"curl_cffi:{self.impersonate_target}"
        return "aiohttp"

    @property
    def endpoint(self) -> str:
        """请求实际发往的地址前缀。"""
        return self.reverse_proxy or self.base_url

    def describe_transport(self) -> str:
        """一行人类可读的链路描述，用于 `搜本子 统计`。"""
        route = "直连 soutubot.moe"
        if self.reverse_proxy:
            route = f"反代 {self.reverse_proxy}"
        elif self.proxy:
            route = "HTTP 代理"
        return f"{self.transport} / {route}"

    def _can_escalate(self) -> bool:
        """是否还能把传输层升级到 curl_cffi。"""
        if self._curl_active or self.reverse_proxy:
            # 走反代时，本机 TLS 指纹对 soutubot 不可见，升级没有意义
            return False
        if self.impersonate_mode != IMPERSONATE_AUTO:
            return False
        if self._curl_session is not None:
            return True
        return self._transport_managed and curl_cffi_available()

    def _escalate(self) -> bool:
        """遇到 403 时切换到 curl_cffi。最多成功一次，不会形成死循环。"""
        if not self._can_escalate():
            return False
        self._curl_active = True
        # 旧的 boot token 是另一条连接拿到的，作废重取更稳
        self._boot_token = None
        self._boot_token_at = 0.0
        return True

    # ---- 生命周期 ----

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
            self._owns_session = True
        return self._session

    async def _ensure_curl_session(self) -> Any:
        if self._curl_session is not None:
            return self._curl_session
        try:
            from curl_cffi.requests import AsyncSession  # type: ignore
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise SoutubotNetworkError(
                "需要 curl_cffi 才能伪装浏览器指纹，请执行 pip install curl_cffi"
            ) from exc
        self._curl_session = AsyncSession()
        self._owns_curl_session = True
        return self._curl_session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        if self._owns_curl_session and self._curl_session is not None:
            closer = getattr(self._curl_session, "close", None)
            if callable(closer):
                try:
                    outcome = closer()
                    if inspect.isawaitable(outcome):
                        await outcome
                except Exception:  # pragma: no cover - 关闭失败无需影响调用方
                    pass
        self._curl_session = None

    # ---- 请求头 ----

    _HOME_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

    def _decorate(self, headers: dict[str, str]) -> dict[str, str]:
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.reverse_proxy and self.reverse_proxy_token:
            # 让反代能校验来源，避免被别人当公共代理白嫖
            headers["X-Proxy-Token"] = self.reverse_proxy_token
        return headers

    def _home_headers(self) -> dict[str, str]:
        return self._decorate(
            {
                "User-Agent": self.user_agent,
                "Accept": self._HOME_ACCEPT,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            }
        )

    def _base_headers(self) -> dict[str, str]:
        return self._decorate(
            {
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": f"{self.base_url}/",
                "Origin": self.base_url,
                "X-Requested-With": "XMLHttpRequest",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self.endpoint}{path}"

    def _diagnose(self, status: int, headers: Any, text: str) -> dict[str, Any]:
        detail = collect_diagnostics(status, headers, text)
        detail["transport"] = self.transport
        detail["via"] = "reverse_proxy" if self.reverse_proxy else "direct"
        self.last_failure = detail
        return detail

    # ---- 底层收发 ----

    async def _curl_fetch(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> _HttpResponse:
        session = await self._ensure_curl_session()
        kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": self.timeout,
            "impersonate": self.impersonate_target,
        }
        if self.proxy:
            kwargs["proxy"] = self.proxy
        if body is not None:
            kwargs["data"] = body
        try:
            response = await session.request(method, url, **kwargs)
        except SoutubotError:
            raise
        except asyncio.TimeoutError as exc:
            raise SoutubotNetworkError("请求 soutubot 超时") from exc
        except Exception as exc:  # curl_cffi 的异常层级独立于 aiohttp
            raise SoutubotNetworkError(f"请求 soutubot 失败：{exc}") from exc
        return _HttpResponse(
            getattr(response, "status_code", 0),
            getattr(response, "headers", {}),
            getattr(response, "text", "") or "",
        )

    async def _fetch_home(self) -> _HttpResponse:
        url = f"{self.endpoint}/"
        headers = self._home_headers()
        if self._curl_active:
            return await self._curl_fetch("GET", url, headers=headers)
        session = await self._ensure_session()
        try:
            async with session.get(url, headers=headers, proxy=self.proxy) as response:
                return _HttpResponse(
                    response.status, response.headers, await response.text(errors="ignore")
                )
        except aiohttp.ClientError as exc:
            raise SoutubotNetworkError(f"无法访问 soutubot：{exc}") from exc
        except asyncio.TimeoutError as exc:
            raise SoutubotNetworkError("访问 soutubot 首页超时") from exc

    async def _fetch_api(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> _HttpResponse:
        if self._curl_active:
            return await self._curl_fetch(method, url, headers=headers, body=body)
        session = await self._ensure_session()
        try:
            async with session.request(
                method, url, data=body, headers=headers, proxy=self.proxy
            ) as response:
                return _HttpResponse(
                    response.status, response.headers, await response.text(errors="ignore")
                )
        except aiohttp.ClientError as exc:
            raise SoutubotNetworkError(f"请求 soutubot 失败：{exc}") from exc
        except asyncio.TimeoutError as exc:
            raise SoutubotNetworkError("请求 soutubot 超时") from exc

    # ---- 签名 ----

    async def _get_boot_token(self, force: bool = False) -> int:
        async with self._boot_lock:
            fresh = (
                self._boot_token is not None
                and (time.monotonic() - self._boot_token_at) < _BOOT_TOKEN_TTL
            )
            if fresh and not force:
                return self._boot_token  # type: ignore[return-value]

            attempt = 0
            last_error: SoutubotError | None = None
            while True:
                response = await self._fetch_home()
                if response.status == 200:
                    self.last_failure = None
                    self._boot_token = extract_boot_token(response.text)
                    self._boot_token_at = time.monotonic()
                    return self._boot_token

                detail = self._diagnose(
                    response.status, response.headers, response.text
                )
                if response.status == 403:
                    kind = classify_forbidden(response.headers, response.text)
                    detail["kind"] = kind
                    last_error = SoutubotBlockedError(
                        forbidden_message(kind, detail, where="访问 soutubot 首页"),
                        kind=kind,
                        detail=detail,
                    )
                    if self._escalate():
                        # 换传输层重试不消耗退避预算
                        continue
                elif response.status == 429:
                    last_error = SoutubotRateLimitError(
                        "soutubot 提示请求过于频繁（HTTP 429），请稍后再试。"
                    )
                elif 500 <= response.status < 600:
                    last_error = SoutubotUpstreamError(
                        f"访问 soutubot 首页失败：HTTP {response.status}"
                        f"｜诊断：{format_diagnostics(detail)}"
                    )
                else:
                    raise SoutubotUpstreamError(
                        f"访问 soutubot 首页失败：HTTP {response.status}"
                        f"｜诊断：{format_diagnostics(detail)}"
                    )

                if attempt >= self.max_retries:
                    break
                attempt += 1
                await asyncio.sleep(min(4.0, 1.0 * (2**attempt - 1)))

            raise last_error or SoutubotUpstreamError("访问 soutubot 首页失败")

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
        url = self._url(path)
        last_error: SoutubotError | None = None
        attempt = 0

        while True:
            headers = await self._auth_headers(force_refresh=attempt > 0)
            if content_type:
                headers["Content-Type"] = content_type

            try:
                response = await self._fetch_api(
                    method, url, headers=headers, body=body
                )
            except SoutubotNetworkError as exc:
                last_error = exc
            else:
                status = response.status
                if status == 200:
                    self.last_failure = None
                    return _parse_json(response.text)

                detail = self._diagnose(status, response.headers, response.text)
                if status == 403:
                    kind = classify_forbidden(response.headers, response.text)
                    detail["kind"] = kind
                    last_error = SoutubotBlockedError(
                        forbidden_message(kind, detail, where="请求 soutubot 接口"),
                        kind=kind,
                        detail=detail,
                    )
                    if self._escalate():
                        continue
                elif status == 401:
                    last_error = SoutubotAuthError(
                        "soutubot 拒绝了签名（HTTP 401）。"
                        "常见原因是本机系统时间不准，请校准 NTP 后重试。"
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
                    raise SoutubotUpstreamError(
                        f"soutubot 返回了意外状态码 HTTP {status}"
                        f"｜诊断：{format_diagnostics(detail)}"
                    )

            if attempt >= self.max_retries:
                break
            attempt += 1
            await asyncio.sleep(min(4.0, 1.0 * (2**attempt - 1)))

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
