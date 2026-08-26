"""client.py：签名算法、boot token 解析、multipart 组装与状态码映射（全离线）。"""

from __future__ import annotations

import asyncio
import base64
import re
import uuid

import aiohttp
import pytest

from soutubot.client import (
    DEFAULT_BASE_URL,
    DEFAULT_USER_AGENT,
    FACTOR_NORMAL,
    FACTOR_STRICT,
    SoutubotAuthError,
    SoutubotClient,
    SoutubotError,
    SoutubotNetworkError,
    SoutubotRateLimitError,
    SoutubotUpstreamError,
    build_api_key,
    extract_boot_token,
)
from soutubot.models import SoutubotSearchResult

from conftest import make_payload

BOOT_TOKEN = 1848392517136
HOME_HTML = (
    "<!doctype html><html><head><title>soutubot</title></head><body>"
    "<script>window.GLOBAL = { siteConfig: {\"locale\":\"zh\"}, m: "
    + str(BOOT_TOKEN)
    + " };</script></body></html>"
)


# ---------------------------------------------------------------- 常量


def test_module_constants():
    assert DEFAULT_BASE_URL == "https://soutubot.moe"
    assert FACTOR_NORMAL == 1.2
    assert FACTOR_STRICT == 1.4
    assert "Mozilla/5.0" in DEFAULT_USER_AGENT


# ---------------------------------------------------------------- build_api_key


def test_build_api_key_known_vector():
    assert build_api_key(1848392517136, "abc", now=1700000000) == (
        "QN0EzNxUjM5MDO0gTMwADM5gjM"
    )


@pytest.mark.parametrize(
    ("token", "ua", "now"),
    [
        (1848392517136, "abc", 1700000000),
        (1848392517136, DEFAULT_USER_AGENT, 1766000000),
        (100000, "", 0),
        (999999999999, "x" * 250, 2000000000),
    ],
)
def test_build_api_key_matches_reference_formula(token, ua, now):
    raw = str(int(now) ** 2 + len(ua) ** 2 + token)
    expected = base64.b64encode(raw.encode()).decode()[::-1].replace("=", "")
    assert build_api_key(token, ua, now=now) == expected
    assert "=" not in build_api_key(token, ua, now=now)


def test_build_api_key_truncates_float_timestamp():
    assert build_api_key(1, "ua", now=1700000000.987) == build_api_key(
        1, "ua", now=1700000000
    )


def test_build_api_key_depends_on_user_agent_length_only():
    same_length = build_api_key(BOOT_TOKEN, "abc", now=1700000000)
    other_same_length = build_api_key(BOOT_TOKEN, "xyz", now=1700000000)
    longer = build_api_key(BOOT_TOKEN, "abcd", now=1700000000)
    assert same_length == other_same_length  # 只吃长度，不吃内容
    assert longer != same_length


def test_build_api_key_depends_on_token_and_time():
    base = build_api_key(BOOT_TOKEN, "ua", now=1700000000)
    assert build_api_key(BOOT_TOKEN + 1, "ua", now=1700000000) != base
    assert build_api_key(BOOT_TOKEN, "ua", now=1700000001) != base


def test_build_api_key_uses_current_time_by_default(monkeypatch):
    monkeypatch.setattr("soutubot.client.time.time", lambda: 1700000000.5)
    assert build_api_key(1848392517136, "abc") == "QN0EzNxUjM5MDO0gTMwADM5gjM"


def test_build_api_key_is_reversible_to_raw_number():
    key = build_api_key(BOOT_TOKEN, "abc", now=1700000000)
    padded = key[::-1]
    padded += "=" * (-len(padded) % 4)
    decoded = base64.b64decode(padded).decode()
    assert int(decoded) == 1700000000**2 + 3**2 + BOOT_TOKEN


# ---------------------------------------------------------------- boot token


def test_extract_boot_token_from_realistic_home_page():
    assert extract_boot_token(HOME_HTML) == BOOT_TOKEN


@pytest.mark.parametrize(
    "html",
    [
        "window.GLOBAL={m:1848392517136}",
        "window.GLOBAL = { m : 1848392517136 , siteConfig: {} }",
        'var x={"a":1};window.GLOBAL = {siteConfig:{},m:123456};',
    ],
)
def test_extract_boot_token_variants(html):
    assert extract_boot_token(html) > 0


@pytest.mark.parametrize(
    "html",
    [
        "",
        None,
        "<html><body>no token here</body></html>",
        "window.GLOBAL = { m: 12345 }",  # 少于 6 位不算 token
        "window.GLOBAL = { m: 'abcdef' }",
    ],
)
def test_extract_boot_token_failure_raises_upstream_error(html):
    with pytest.raises(SoutubotUpstreamError):
        extract_boot_token(html)


# ---------------------------------------------------------------- multipart


IMAGE_BYTES = b"\xff\xd8\xff\xe0" + b"\r\n--fake-boundary\r\n" + bytes(range(256))


def test_build_multipart_structure():
    body, content_type = SoutubotClient._build_multipart(
        IMAGE_BYTES, FACTOR_STRICT, "blob.jpg", "image/jpeg"
    )
    match = re.fullmatch(r"multipart/form-data; boundary=([0-9a-f]{32})", content_type)
    assert match, content_type
    boundary = match.group(1)

    # 与 uuid4().hex 同形态
    uuid.UUID(hex=boundary)

    assert b'name="file"' in body
    assert b'filename="blob.jpg"' in body
    assert b"Content-Type: image/jpeg" in body
    assert b'name="factor"' in body
    assert b"1.4" in body

    marker = f"--{boundary}".encode()
    assert body.startswith(marker + b"\r\n")
    assert body.endswith(f"--{boundary}--\r\n".encode())
    assert body.count(marker) == 3  # 两个字段的起始 + 结束边界


def test_build_multipart_keeps_image_bytes_intact():
    body, _ = SoutubotClient._build_multipart(
        IMAGE_BYTES, FACTOR_NORMAL, "x.png", "image/png"
    )
    assert IMAGE_BYTES in body

    header_end = body.index(b"\r\n\r\n") + 4
    extracted = body[header_end : header_end + len(IMAGE_BYTES)]
    assert extracted == IMAGE_BYTES


def test_build_multipart_factor_rendering():
    body, _ = SoutubotClient._build_multipart(b"img", 1.2, "b.jpg", "image/jpeg")
    assert b'name="factor"\r\n\r\n1.2\r\n' in body


def test_build_multipart_boundary_is_unique_per_call():
    _, ct1 = SoutubotClient._build_multipart(b"a", 1.2, "a.jpg", "image/jpeg")
    _, ct2 = SoutubotClient._build_multipart(b"a", 1.2, "a.jpg", "image/jpeg")
    assert ct1 != ct2


def test_build_multipart_escapes_nothing_but_embeds_filename():
    body, _ = SoutubotClient._build_multipart(
        b"img", 1.2, "\u56fe\u7247.webp", "image/webp"
    )
    assert 'filename="\u56fe\u7247.webp"'.encode("utf-8") in body


# ---------------------------------------------------------------- 假 session


class _FakeResponse:
    def __init__(self, status: int, text: str = "", headers: dict | None = None):
        self.status = status
        self._text = text
        self.headers = headers or {}

    async def text(self, errors: str = "strict") -> str:
        return self._text


class _FakeCtx:
    """模拟 aiohttp 的 request/get 返回值（async context manager）。"""

    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome

    async def __aexit__(self, *exc_info):
        return False


class FakeSession:
    """最小可用的 aiohttp.ClientSession 替身，完全离线。"""

    def __init__(self, outcomes=None, *, home_outcomes=None):
        self.closed = False
        self.outcomes = list(outcomes or [])
        self.home_outcomes = list(home_outcomes or [])
        self.api_calls: list[dict] = []
        self.home_calls: list[dict] = []

    # 首页（boot token）
    def get(self, url, **kwargs):
        self.home_calls.append({"url": url, **kwargs})
        if self.home_outcomes:
            outcome = self.home_outcomes.pop(0)
        else:
            outcome = _FakeResponse(200, HOME_HTML)
        return _FakeCtx(outcome)

    # /api/*
    def request(self, method, url, **kwargs):
        self.api_calls.append({"method": method, "url": url, **kwargs})
        if not self.outcomes:
            raise AssertionError(f"未预期的额外请求：{method} {url}")
        return _FakeCtx(self.outcomes.pop(0))

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """把退避等待换成 no-op，避免测试真的睡觉。"""

    async def _instant(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


def make_client(session: FakeSession, **kwargs) -> SoutubotClient:
    kwargs.setdefault("max_retries", 1)
    return SoutubotClient(session=session, **kwargs)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 构造参数


def test_client_normalizes_init_arguments():
    client = SoutubotClient(
        base_url="https://mirror.example.com/",
        user_agent="",
        timeout=1.0,
        proxy="",
        max_retries=-5,
        session=FakeSession(),
    )
    assert client.base_url == "https://mirror.example.com"
    assert client.user_agent == DEFAULT_USER_AGENT
    assert client.timeout == 5.0  # 下限 5s
    assert client.proxy is None
    assert client.max_retries == 0


# ---------------------------------------------------------------- 成功路径


def test_search_success_and_request_shape():
    import json

    payload = make_payload()
    session = FakeSession([_FakeResponse(200, json.dumps(payload))])
    client = make_client(session, proxy="http://127.0.0.1:7890")

    result = run(client.search(b"\xff\xd8\xff\xe0image", factor=FACTOR_STRICT))
    assert isinstance(result, SoutubotSearchResult)
    assert result.result_id == payload["id"]
    assert len(result.matches) == 3

    assert len(session.home_calls) == 1  # 先抓首页拿签名
    call = session.api_calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://soutubot.moe/api/search"
    assert call["proxy"] == "http://127.0.0.1:7890"
    assert call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert call["headers"]["X-API-KEY"]
    assert call["headers"]["Referer"] == "https://soutubot.moe/"
    assert call["headers"]["Origin"] == "https://soutubot.moe"
    assert b'name="factor"' in call["data"]


def test_fetch_result_success():
    import json

    session = FakeSession([_FakeResponse(200, json.dumps(make_payload()))])
    client = make_client(session)
    result = run(client.fetch_result("2026082615542720"))
    assert result.result_id == "2026082615542720"
    assert session.api_calls[0]["method"] == "GET"
    assert (
        session.api_calls[0]["url"]
        == "https://soutubot.moe/api/results/2026082615542720"
    )
    assert session.api_calls[0]["data"] is None


def test_boot_token_is_cached_between_requests():
    import json

    body = json.dumps(make_payload())
    session = FakeSession([_FakeResponse(200, body), _FakeResponse(200, body)])
    client = make_client(session)
    run(client.search(b"img"))
    run(client.search(b"img"))
    assert len(session.home_calls) == 1  # TTL 内不再打首页
    assert len(session.api_calls) == 2


# ---------------------------------------------------------------- 参数校验


def test_search_rejects_empty_image():
    client = make_client(FakeSession())
    with pytest.raises(SoutubotError):
        run(client.search(b""))


@pytest.mark.parametrize("bad_id", ["", "   ", "abc", "20260826-1", None])
def test_fetch_result_rejects_invalid_id(bad_id):
    client = make_client(FakeSession())
    with pytest.raises(SoutubotError):
        run(client.fetch_result(bad_id))


# ---------------------------------------------------------------- 状态码映射


def test_401_maps_to_auth_error_and_refreshes_signature():
    session = FakeSession([_FakeResponse(401, "denied"), _FakeResponse(401, "denied")])
    client = make_client(session, max_retries=1)
    with pytest.raises(SoutubotAuthError):
        run(client.search(b"img"))
    assert len(session.api_calls) == 2  # 重试了一次
    assert len(session.home_calls) == 2  # 401 后强制刷新 boot token


def test_429_maps_to_rate_limit_error():
    session = FakeSession([_FakeResponse(429, ""), _FakeResponse(429, "")])
    client = make_client(session, max_retries=1)
    with pytest.raises(SoutubotRateLimitError):
        run(client.search(b"img"))
    assert len(session.api_calls) == 2


@pytest.mark.parametrize("status", [413, 415, 422])
def test_image_rejection_statuses_do_not_retry(status):
    session = FakeSession([_FakeResponse(status, "")])
    client = make_client(session, max_retries=3)
    with pytest.raises(SoutubotUpstreamError):
        run(client.search(b"img"))
    assert len(session.api_calls) == 1  # 图片问题重试无意义


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_retries_then_raises_upstream_error(status):
    session = FakeSession([_FakeResponse(status, ""), _FakeResponse(status, "")])
    client = make_client(session, max_retries=1)
    with pytest.raises(SoutubotUpstreamError):
        run(client.search(b"img"))
    assert len(session.api_calls) == 2


@pytest.mark.parametrize("status", [301, 404, 418])
def test_unexpected_status_raises_immediately(status):
    session = FakeSession([_FakeResponse(status, "")])
    client = make_client(session, max_retries=3)
    with pytest.raises(SoutubotUpstreamError):
        run(client.search(b"img"))
    assert len(session.api_calls) == 1


def test_retry_recovers_after_transient_5xx():
    import json

    session = FakeSession(
        [_FakeResponse(503, ""), _FakeResponse(200, json.dumps(make_payload()))]
    )
    client = make_client(session, max_retries=2)
    result = run(client.search(b"img"))
    assert result.result_id == "2026082615542720"
    assert len(session.api_calls) == 2


def test_no_retry_when_max_retries_zero():
    session = FakeSession([_FakeResponse(500, "")])
    client = make_client(session, max_retries=0)
    with pytest.raises(SoutubotUpstreamError):
        run(client.search(b"img"))
    assert len(session.api_calls) == 1


# ---------------------------------------------------------------- 网络与解析


def test_client_error_maps_to_network_error():
    session = FakeSession(
        [
            aiohttp.ClientConnectionError("connection refused"),
            aiohttp.ClientConnectionError("connection refused"),
        ]
    )
    client = make_client(session, max_retries=1)
    with pytest.raises(SoutubotNetworkError):
        run(client.search(b"img"))
    assert len(session.api_calls) == 2


def test_timeout_maps_to_network_error():
    session = FakeSession([asyncio.TimeoutError(), asyncio.TimeoutError()])
    client = make_client(session, max_retries=1)
    with pytest.raises(SoutubotNetworkError) as excinfo:
        run(client.search(b"img"))
    assert "超时" in str(excinfo.value)


@pytest.mark.parametrize("text", ["not json at all", "", "<html>502</html>"])
def test_unparsable_body_raises_upstream_error(text):
    session = FakeSession([_FakeResponse(200, text)])
    client = make_client(session, max_retries=0)
    with pytest.raises(SoutubotUpstreamError):
        run(client.search(b"img"))


@pytest.mark.parametrize("text", ["[1,2,3]", '"string"', "null"])
def test_non_object_json_raises_upstream_error(text):
    session = FakeSession([_FakeResponse(200, text)])
    client = make_client(session, max_retries=0)
    with pytest.raises(SoutubotUpstreamError):
        run(client.search(b"img"))


def test_home_page_non_200_raises_upstream_error():
    session = FakeSession(home_outcomes=[_FakeResponse(403, "blocked")])
    client = make_client(session)
    with pytest.raises(SoutubotUpstreamError):
        run(client.search(b"img"))
    assert session.api_calls == []  # 拿不到签名就不该发 API 请求


def test_home_page_without_token_raises_upstream_error():
    session = FakeSession(home_outcomes=[_FakeResponse(200, "<html>nope</html>")])
    client = make_client(session)
    with pytest.raises(SoutubotUpstreamError):
        run(client.search(b"img"))


def test_home_page_network_error_maps_to_network_error():
    session = FakeSession(home_outcomes=[aiohttp.ClientConnectionError("dns fail")])
    client = make_client(session)
    with pytest.raises(SoutubotNetworkError):
        run(client.search(b"img"))


def test_close_does_not_close_injected_session():
    session = FakeSession()
    client = make_client(session)
    run(client.close())
    assert session.closed is False  # 外部传入的 session 不该被客户端关闭
