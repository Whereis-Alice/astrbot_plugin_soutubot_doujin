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
    DEFAULT_IMPERSONATE_TARGET,
    DEFAULT_USER_AGENT,
    FACTOR_NORMAL,
    FACTOR_STRICT,
    IMPERSONATE_AUTO,
    IMPERSONATE_MODES,
    IMPERSONATE_OFF,
    IMPERSONATE_ON,
    SoutubotAuthError,
    SoutubotBlockedError,
    SoutubotClient,
    SoutubotError,
    SoutubotNetworkError,
    SoutubotRateLimitError,
    SoutubotUpstreamError,
    build_api_key,
    classify_forbidden,
    collect_diagnostics,
    curl_cffi_available,
    extract_boot_token,
    forbidden_message,
    format_diagnostics,
    summarize_body,
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


class FakeCurlResponse:
    """curl_cffi 的响应形态：status_code / headers / text（同步属性）。"""

    def __init__(self, status_code: int, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeCurlSession:
    """curl_cffi.requests.AsyncSession 的替身，完全离线。"""

    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls: list[dict] = []
        self.closed = False

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        outcome = self.outcomes.pop(0) if self.outcomes else FakeCurlResponse(200, HOME_HTML)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

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


def test_home_page_403_maps_to_blocked_error():
    session = FakeSession(home_outcomes=[_FakeResponse(403, "blocked")])
    client = make_client(session, max_retries=0)
    with pytest.raises(SoutubotBlockedError):
        run(client.search(b"img"))
    assert session.api_calls == []  # 拿不到签名就不该发 API 请求


def test_home_page_5xx_retries_then_raises_upstream_error():
    session = FakeSession(
        home_outcomes=[_FakeResponse(503, "oops"), _FakeResponse(503, "oops")]
    )
    client = make_client(session, max_retries=1)
    with pytest.raises(SoutubotUpstreamError) as excinfo:
        run(client.search(b"img"))
    assert "HTTP 503" in str(excinfo.value)
    assert len(session.home_calls) == 2
    assert session.api_calls == []


def test_home_page_unexpected_status_raises_without_retry():
    session = FakeSession(home_outcomes=[_FakeResponse(404, "nope")])
    client = make_client(session, max_retries=2)
    with pytest.raises(SoutubotUpstreamError) as excinfo:
        run(client.search(b"img"))
    assert "HTTP 404" in str(excinfo.value)
    assert len(session.home_calls) == 1


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


# ------------------------------------------------------- 403 诊断与分类（纯函数）


def test_summarize_body_strips_markup_and_collapses_space():
    html = (
        "<html><head><style>body{color:red}</style>"
        "<script>var a = 1;</script></head><body>\n\n"
        "  Just a   moment...  <p>Enable JavaScript</p></body></html>"
    )
    assert summarize_body(html) == "Just a moment... Enable JavaScript"


def test_summarize_body_truncates_with_ellipsis():
    out = summarize_body("x" * 500, limit=50)
    assert len(out) == 51 and out.endswith("\u2026")


def test_summarize_body_handles_empty():
    assert summarize_body("") == ""
    assert summarize_body(None) == ""


def test_collect_diagnostics_picks_useful_headers():
    detail = collect_diagnostics(
        403,
        {
            "CF-RAY": "9abc-SIN",
            "cf-mitigated": "challenge",
            "Server": "cloudflare",
            "X-Irrelevant": "drop me",
        },
        "<html>Just a moment...</html>",
    )
    assert detail["status"] == 403
    assert detail["cf-ray"] == "9abc-SIN"
    assert detail["cf-mitigated"] == "challenge"
    assert detail["server"] == "cloudflare"
    assert "x-irrelevant" not in detail
    assert detail["body"] == "Just a moment..."


def test_collect_diagnostics_tolerates_missing_headers():
    detail = collect_diagnostics(500)
    assert detail == {"status": 500}


def test_format_diagnostics_is_single_line_key_value():
    text = format_diagnostics({"status": 403, "cf-ray": "abc"})
    assert "status=403" in text and "cf-ray='abc'" in text
    assert "\n" not in text


@pytest.mark.parametrize(
    ("headers", "body", "expected"),
    [
        ({"cf-mitigated": "challenge"}, "", "challenge"),
        ({}, "<title>Just a moment...</title>", "challenge"),
        ({}, "please enable JavaScript and cookies to continue", "challenge"),
        ({}, "Sorry, you have been blocked", "waf"),
        ({}, "Attention Required! | Cloudflare", "waf"),
        ({}, "Error 1020 Ray ID", "waf"),
        ({}, "This service is not available in your country", "geo"),
        ({"server": "cloudflare"}, "whatever", "cloudflare"),
        ({"CF-RAY": "abc-SIN"}, "whatever", "cloudflare"),
        ({"server": "nginx"}, "plain forbidden", "origin"),
        ({}, "", "origin"),
    ],
)
def test_classify_forbidden(headers, body, expected):
    assert classify_forbidden(headers, body) == expected


def test_forbidden_message_mentions_ray_and_advice():
    detail = collect_diagnostics(403, {"cf-ray": "9abc-SIN"}, "Just a moment...")
    message = forbidden_message("challenge", detail, where="\u8bbf\u95ee\u9996\u9875")
    assert message.startswith("\u8bbf\u95ee\u9996\u9875\u88ab\u62d2\u7edd")
    assert "HTTP 403" in message
    assert "9abc-SIN" in message
    assert "tls_impersonate" in message


def test_forbidden_message_waf_advice_points_at_proxy_option():
    message = forbidden_message("waf", {"status": 403}, where="\u8bf7\u6c42\u63a5\u53e3")
    assert "proxy" in message
    assert "tls_impersonate" not in message


# ------------------------------------------------------------------ 传输层伪装


def test_impersonate_mode_defaults():
    client = SoutubotClient(session=FakeSession())
    assert client.impersonate_mode == IMPERSONATE_AUTO
    assert client.impersonate_target == DEFAULT_IMPERSONATE_TARGET
    assert client.transport == "aiohttp"
    assert client.endpoint == DEFAULT_BASE_URL
    assert IMPERSONATE_MODES == (IMPERSONATE_OFF, IMPERSONATE_AUTO, IMPERSONATE_ON)
    assert isinstance(curl_cffi_available(), bool)


def test_impersonate_off_never_escalates():
    client = SoutubotClient(session=FakeSession(), impersonate=IMPERSONATE_OFF)
    assert client._can_escalate() is False
    assert client._escalate() is False
    assert client.transport == "aiohttp"


def test_injected_session_disables_auto_escalation():
    # 单元测试注入了 session，说明传输层由调用方掌管，不能偷偷换成 curl_cffi
    client = SoutubotClient(session=FakeSession(), impersonate=IMPERSONATE_AUTO)
    assert client._can_escalate() is False


def test_custom_impersonate_target_is_passed_through():
    client = SoutubotClient(
        session=FakeSession(), impersonate="chrome124", curl_session=FakeCurlSession()
    )
    assert client.impersonate_target == "chrome124"
    assert client.transport == "curl_cffi:chrome124"


def test_requests_always_target_the_site_directly():
    import json

    session = FakeSession([_FakeResponse(200, json.dumps(make_payload()))])
    client = make_client(session)
    run(client.search(b"img"))

    assert client.endpoint == "https://soutubot.moe"
    assert session.home_calls[0]["url"] == "https://soutubot.moe/"
    call = session.api_calls[0]
    assert call["url"] == "https://soutubot.moe/api/search"
    assert call["headers"]["Referer"] == "https://soutubot.moe/"
    assert call["headers"]["Origin"] == "https://soutubot.moe"
    assert "X-Proxy-Token" not in call["headers"]
    assert "\u76f4\u8fde soutubot.moe" in client.describe_transport()


def test_describe_transport_reports_http_proxy():
    client = make_client(FakeSession(), proxy="http://127.0.0.1:7890")
    assert "HTTP \u4ee3\u7406" in client.describe_transport()


def test_extra_cookie_is_attached_to_both_requests():
    import json

    session = FakeSession([_FakeResponse(200, json.dumps(make_payload()))])
    client = make_client(session, cookie="cf_clearance=abc")
    run(client.search(b"img"))
    assert session.home_calls[0]["headers"]["Cookie"] == "cf_clearance=abc"
    assert session.api_calls[0]["headers"]["Cookie"] == "cf_clearance=abc"


def test_last_failure_records_diagnostics_and_is_cleared_on_success():
    import json

    session = FakeSession(
        [_FakeResponse(200, json.dumps(make_payload()))],
        home_outcomes=[_FakeResponse(403, "Just a moment...", {"cf-ray": "9x-SIN"})],
    )
    client = make_client(session, max_retries=1)
    run(client.search(b"img"))  # 第二次首页请求回落到 200
    assert client.last_failure is None  # 成功后清空

    session2 = FakeSession(home_outcomes=[_FakeResponse(403, "Just a moment...", {"cf-ray": "9x-SIN"})])
    client2 = make_client(session2, max_retries=0)
    with pytest.raises(SoutubotBlockedError) as excinfo:
        run(client2.search(b"img"))
    assert excinfo.value.kind == "challenge"
    assert excinfo.value.detail["cf-ray"] == "9x-SIN"
    assert client2.last_failure["transport"] == "aiohttp"
    assert "via" not in client2.last_failure


def test_api_403_maps_to_blocked_error():
    session = FakeSession([_FakeResponse(403, "Sorry, you have been blocked")])
    client = make_client(session, max_retries=0)
    with pytest.raises(SoutubotBlockedError) as excinfo:
        run(client.search(b"img"))
    assert excinfo.value.kind == "waf"
    assert "HTTP 403" in str(excinfo.value)


def test_blocked_error_is_soutubot_error():
    exc = SoutubotBlockedError("boom")
    assert isinstance(exc, SoutubotError)
    assert exc.kind == "unknown" and exc.detail == {}


# ------------------------------------------------------------ curl_cffi 传输层


def test_curl_transport_used_when_impersonate_on():
    import json

    curl = FakeCurlSession(
        [
            FakeCurlResponse(200, HOME_HTML),
            FakeCurlResponse(200, json.dumps(make_payload())),
        ]
    )
    session = FakeSession()
    client = SoutubotClient(
        session=session,
        curl_session=curl,
        impersonate=IMPERSONATE_ON,
        max_retries=0,
    )
    result = run(client.search(b"img", factor=FACTOR_STRICT))
    assert isinstance(result, SoutubotSearchResult)
    assert session.api_calls == [] and session.home_calls == []
    assert [call["method"] for call in curl.calls] == ["GET", "POST"]
    assert curl.calls[0]["impersonate"] == DEFAULT_IMPERSONATE_TARGET
    assert curl.calls[1]["data"].startswith(b"--")
    assert "proxy" not in curl.calls[0]


def test_curl_transport_passes_proxy_when_configured():
    curl = FakeCurlSession([FakeCurlResponse(200, HOME_HTML)])
    client = SoutubotClient(
        session=FakeSession(),
        curl_session=curl,
        impersonate=IMPERSONATE_ON,
        proxy="http://127.0.0.1:7890",
    )
    run(client._get_boot_token())
    assert curl.calls[0]["proxy"] == "http://127.0.0.1:7890"


def test_auto_escalates_to_curl_on_403_without_spending_retries():
    import json

    curl = FakeCurlSession(
        [
            FakeCurlResponse(200, HOME_HTML),
            FakeCurlResponse(200, json.dumps(make_payload())),
        ]
    )
    session = FakeSession(home_outcomes=[_FakeResponse(403, "Just a moment...")])
    client = SoutubotClient(
        session=session,
        curl_session=curl,
        impersonate=IMPERSONATE_AUTO,
        max_retries=0,  # 升级传输层不占用重试预算
    )
    assert client.transport == "aiohttp"
    result = run(client.search(b"img"))
    assert isinstance(result, SoutubotSearchResult)
    assert client.transport == f"curl_cffi:{DEFAULT_IMPERSONATE_TARGET}"
    assert len(session.home_calls) == 1  # aiohttp 只试了一次
    assert len(curl.calls) == 2
    assert client._can_escalate() is False  # 只升级一次


def test_curl_network_failure_maps_to_network_error():
    curl = FakeCurlSession([RuntimeError("tls handshake failed")])
    client = SoutubotClient(
        session=FakeSession(), curl_session=curl, impersonate=IMPERSONATE_ON
    )
    with pytest.raises(SoutubotNetworkError):
        run(client._get_boot_token())


def test_close_does_not_close_injected_curl_session():
    curl = FakeCurlSession()
    client = SoutubotClient(
        session=FakeSession(), curl_session=curl, impersonate=IMPERSONATE_ON
    )
    run(client.close())
    assert curl.closed is False
