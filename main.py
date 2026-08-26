"""AstrBot 插件入口：搜图Bot酱（soutubot.moe）本子以图搜书。

本文件只负责与 AstrBot 交互（命令、LLM 工具、消息发送、配置、限流），
真正的接口调用与结果渲染在 soutubot/ 子包里，可脱离 AstrBot 单独测试。
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlencode
from typing import Any

import aiohttp
from pydantic import Field
from pydantic.dataclasses import dataclass

import astrbot.api.message_components as Comp
from astrbot.api import FunctionTool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image as MsgImage
from astrbot.api.message_components import Reply
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.message import TextPart
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

from .soutubot import (
    DEFAULT_BASE_URL,
    DEFAULT_USER_AGENT,
    FACTOR_NORMAL,
    FACTOR_STRICT,
    ImageTooLargeError,
    SoutubotAuthError,
    SoutubotBlockedError,
    SoutubotClient,
    SoutubotError,
    SoutubotNetworkError,
    SoutubotRateLimitError,
    SoutubotSearchResult,
    SoutubotUpstreamError,
    curl_cffi_available,
    dedupe_matches,
    describe_mirrors,
    fetch_image_bytes,
    format_diagnostics,
    format_llm_summary,
    format_plain_report,
    local_path_from_reference,
    normalize_reverse_proxy,
    prepare_image,
    read_bool,
    read_int,
    read_list,
    read_str,
    sha256_hex,
)

PLUGIN_ID = "astrbot_plugin_soutubot_doujin"
PLUGIN_AUTHOR = "Huli3"
PLUGIN_DESC = "基于搜图Bot酱（soutubot.moe）的本子以图搜书插件，支持命令搜索与 LLM 工具自主调用"
PLUGIN_VERSION = "1.0.0"
PLUGIN_REPO = "https://github.com/Whereis-Alice/astrbot_plugin_soutubot_doujin"

CONFIG_SECTION = "soutubot_doujin_settings"
TOOL_NAME = "soutubot_search_doujin"
MAIN_COMMAND = "搜本子"
COMMAND_ALIASES = ("soutubot", "搜图bot", "以图搜书")

WAKE_PREFIXES = ("/", "!", "！", "#", ".", "。")

STRICT_WORDS = {"严格", "strict", "精确", "s"}
NORMAL_WORDS = {"普通", "normal", "宽松", "n"}
HELP_WORDS = {"帮助", "help", "?", "？", "用法"}
MIRROR_WORDS = {"镜像", "mirror", "站点"}
STATS_WORDS = {"统计", "stats", "状态", "status"}
RESULT_WORDS = {"结果", "result", "id"}

DEFAULT_TOOL_REQUEST_KEYWORDS = [
    "搜本子",
    "找本子",
    "这是什么本",
    "什么本子",
    "本子名",
    "这本叫什么",
    "出处",
    "出自哪里",
    "搜图",
    "以图搜书",
    "doujin",
    "nhentai",
    "ehentai",
]

DEFAULT_TOOL_DESCRIPTION = (
    "使用搜图Bot酱（soutubot.moe）以图搜本子。"
    "当用户发送或引用一张漫画/本子/同人志的截图或封面，并询问它的名字、出处、是哪一本时调用。"
    "工具会返回若干候选书名、相似度、来源站点与链接。"
    "结果来自图像检索而非你的知识，相似度不高时必须明确告知用户不确定，不要编造书名。"
)

DEFAULT_CONFIG: dict[str, Any] = {
    "strict_mode_default": False,
    "max_results": 5,
    "min_similarity": 28,
    "show_urls": True,
    "show_language": True,
    "mirror_nhentai": "nhentai.net",
    "mirror_ehentai": "e-hentai.org",
    "wait_timeout_seconds": 60,
    "prompt_send_image": "🔍 请发送要搜索的本子图片（60 秒内有效）",
    "prompt_timeout": "⏰ 搜索请求已超时，请重新发送「搜本子」",
    "auto_search_on_image": False,
    "cooldown_seconds": 10,
    "max_concurrency": 2,
    "allowed_sessions": [],
    "blocked_sessions": [],
    "private_only": False,
    "send_preview_image": False,
    "max_preview_images": 1,
    "use_forward_message": False,
    "recall_after_seconds": 0,
    "llm_tool_enabled": True,
    "inject_llm_tool_hint": True,
    "llm_tool_max_results": 5,
    "llm_tool_include_urls": True,
    "tool_request_keywords": DEFAULT_TOOL_REQUEST_KEYWORDS,
    "tool_description": DEFAULT_TOOL_DESCRIPTION,
    "cache_enabled": True,
    "cache_ttl_hours": 72,
    "base_url": DEFAULT_BASE_URL,
    "proxy": "",
    "user_agent": "",
    "request_timeout": 60,
    "max_retries": 2,
    "reverse_proxy_url": "",
    "reverse_proxy_token": "",
    "reverse_proxy_images": False,
    "tls_impersonate": "auto",
    "extra_cookie": "",
}

HELP_TEXT = "\n".join(
    [
        "🔍 搜本子 · 使用说明",
        "",
        "· 搜本子 ＋ 图片：以图搜书（图片可以直接附带，也可以引用别人的消息）",
        "· 搜本子：不带图时，会等你在 60 秒内补发一张图",
        "· 搜本子 严格：使用严格模式，误报更少但更容易搜不到",
        "· 搜本子 普通：强制使用普通模式",
        "· 搜本子 结果 <ID>：用之前的结果 ID 重新查看结果",
        "· 搜本子 镜像：查看当前使用的站点镜像",
        "· 搜本子 统计：查看本插件的调用统计",
        "· 搜本子 帮助：显示这份说明",
        "",
        "另外，只要开启了 LLM 工具，直接对 bot 说「这本叫什么」并配图，它也会自己去搜。",
        "",
        "⚠️ 结果来自成人内容站点的公开索引，相似度不高时请勿当真。",
    ]
)

# 内存缓存最多保留多少张图的结果
_MEMORY_CACHE_LIMIT = 200
_STATS_KV_KEY = "soutubot_doujin_stats"
_CACHE_KV_PREFIX = "soutubot_doujin_cache:"


@dataclass
class SoutubotSearchDoujinTool(FunctionTool[AstrAgentContext]):
    plugin: Any = Field(default=None, repr=False)
    name: str = TOOL_NAME
    description: str = DEFAULT_TOOL_DESCRIPTION
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "image_url": {
                    "type": "string",
                    "description": (
                        "可选。要搜索的图片 URL、本地路径或 file:// URI。"
                        "留空时自动使用当前消息或引用消息中的图片（推荐留空）。"
                    ),
                },
                "strict": {
                    "type": "boolean",
                    "description": (
                        "可选。是否使用严格模式。严格模式误报更少但可能搜不到结果，"
                        "只有在普通模式返回了明显不相关的结果时才设为 true。"
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "可选。最多返回多少条候选，默认使用插件配置。",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs: Any,
    ) -> str:
        if self.plugin is None:
            return "搜本子工具未绑定插件实例，请重载插件。"

        event = getattr(getattr(context, "context", None), "event", None)
        return await self.plugin.search_for_tool(
            event=event,
            image_url=read_str(kwargs.get("image_url")),
            strict=kwargs.get("strict"),
            max_results=kwargs.get("max_results"),
        )


@register(
    PLUGIN_ID,
    PLUGIN_AUTHOR,
    PLUGIN_DESC,
    PLUGIN_VERSION,
    PLUGIN_REPO,
)
class SoutubotDoujinPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        self.config = config or {}

        section = self._section(CONFIG_SECTION)
        for key, default in DEFAULT_CONFIG.items():
            setattr(self, key, section.get(key, default))

        # ---- 归一化配置（面板里填错值也不至于炸掉） ----
        self.strict_mode_default = read_bool(self.strict_mode_default, False)
        self.max_results = read_int(self.max_results, 5, minimum=0, maximum=50)
        self.min_similarity = float(
            read_int(self.min_similarity, 28, minimum=0, maximum=100)
        )
        self.show_urls = read_bool(self.show_urls, True)
        self.show_language = read_bool(self.show_language, True)
        self.wait_timeout_seconds = read_int(
            self.wait_timeout_seconds, 60, minimum=5, maximum=600
        )
        self.prompt_send_image = read_str(
            self.prompt_send_image, DEFAULT_CONFIG["prompt_send_image"]
        )
        self.prompt_timeout = read_str(self.prompt_timeout, "")
        self.auto_search_on_image = read_bool(self.auto_search_on_image, False)
        self.cooldown_seconds = read_int(
            self.cooldown_seconds, 10, minimum=0, maximum=3600
        )
        self.max_concurrency = read_int(self.max_concurrency, 2, minimum=1, maximum=8)
        self.allowed_sessions = read_list(self.allowed_sessions, [])
        self.blocked_sessions = read_list(self.blocked_sessions, [])
        self.private_only = read_bool(self.private_only, False)
        self.send_preview_image = read_bool(self.send_preview_image, False)
        self.max_preview_images = read_int(
            self.max_preview_images, 1, minimum=0, maximum=5
        )
        self.use_forward_message = read_bool(self.use_forward_message, False)
        self.recall_after_seconds = read_int(
            self.recall_after_seconds, 0, minimum=0, maximum=3600
        )
        self.llm_tool_enabled = read_bool(self.llm_tool_enabled, True)
        self.inject_llm_tool_hint = read_bool(self.inject_llm_tool_hint, True)
        self.llm_tool_max_results = read_int(
            self.llm_tool_max_results, 5, minimum=1, maximum=10
        )
        self.llm_tool_include_urls = read_bool(self.llm_tool_include_urls, True)
        self.tool_request_keywords = read_list(
            self.tool_request_keywords, DEFAULT_TOOL_REQUEST_KEYWORDS
        )
        self.tool_description = read_str(
            self.tool_description, DEFAULT_TOOL_DESCRIPTION
        )
        self.cache_enabled = read_bool(self.cache_enabled, True)
        self.cache_ttl_hours = read_int(
            self.cache_ttl_hours, 72, minimum=0, maximum=8760
        )
        self.base_url = read_str(self.base_url, DEFAULT_BASE_URL).rstrip("/")
        self.proxy = read_str(self.proxy, "")
        self.user_agent = read_str(self.user_agent, DEFAULT_USER_AGENT)
        self.request_timeout = read_int(
            self.request_timeout, 60, minimum=10, maximum=300
        )
        self.max_retries = read_int(self.max_retries, 2, minimum=0, maximum=5)
        self.reverse_proxy_url = normalize_reverse_proxy(
            read_str(self.reverse_proxy_url, "")
        )
        self.reverse_proxy_token = read_str(self.reverse_proxy_token, "")
        self.reverse_proxy_images = read_bool(self.reverse_proxy_images, False)
        self.tls_impersonate = read_str(self.tls_impersonate, "auto").lower()
        self.extra_cookie = read_str(self.extra_cookie, "")

        self.mirrors: dict[str, Any] = {
            "nhentai": read_str(self.mirror_nhentai, "nhentai.net"),
            "ehentai": read_str(self.mirror_ehentai, "e-hentai.org"),
            "panda": 0,
        }

        # ---- 运行时状态 ----
        self._client: SoutubotClient | None = None
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._cooldowns: dict[str, float] = {}
        self._memory_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.waiting_sessions: dict[str, dict[str, Any]] = {}
        self.timeout_tasks: dict[str, asyncio.Task] = {}
        self._stats: dict[str, int] = {
            "searches": 0,
            "hits": 0,
            "misses": 0,
            "failures": 0,
            "cache_hits": 0,
            "tool_calls": 0,
        }
        self._stats_dirty = False

        self._register_llm_tool()

    # ------------------------------------------------------------- 基础设施

    def _section(self, key: str) -> dict[str, Any]:
        if hasattr(self.config, "get"):
            value = self.config.get(key, {})
            if isinstance(value, dict):
                return value
        fallback = getattr(self.context, "_config", {})
        if isinstance(fallback, dict):
            value = fallback.get(key, {})
            if isinstance(value, dict):
                return value
        return {}

    def _register_llm_tool(self) -> None:
        try:
            self.context.add_llm_tools(
                SoutubotSearchDoujinTool(
                    plugin=self,
                    description=self.tool_description,
                    active=self.llm_tool_enabled,
                )
            )
        except Exception as exc:
            logger.warning("[%s] 注册 LLM 工具失败：%s", PLUGIN_ID, exc)

    async def initialize(self) -> None:
        await self._load_stats()
        logger.info("[%s] 搜本子插件已加载（v%s）", PLUGIN_ID, PLUGIN_VERSION)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=float(self.request_timeout))
            )
        return self._session

    async def _get_client(self) -> SoutubotClient:
        if self._client is None:
            self._client = SoutubotClient(
                base_url=self.base_url,
                user_agent=self.user_agent,
                timeout=float(self.request_timeout),
                proxy=self.proxy,
                max_retries=self.max_retries,
                reverse_proxy=self.reverse_proxy_url,
                reverse_proxy_token=self.reverse_proxy_token,
                impersonate=self.tls_impersonate,
                cookie=self.extra_cookie,
            )
            logger.info(
                "[%s] soutubot 链路：%s", PLUGIN_ID, self._client.describe_transport()
            )
        return self._client

    async def terminate(self) -> None:
        for task in self.timeout_tasks.values():
            task.cancel()
        self.timeout_tasks.clear()
        self.waiting_sessions.clear()
        await self._save_stats(force=True)
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        logger.info("[%s] 搜本子插件已卸载", PLUGIN_ID)

    # ------------------------------------------------------------- 统计

    async def _load_stats(self) -> None:
        try:
            stored = await self.get_kv_data(_STATS_KV_KEY, None)
        except Exception:
            return
        if isinstance(stored, dict):
            for key in self._stats:
                try:
                    self._stats[key] = int(stored.get(key, self._stats[key]))
                except (TypeError, ValueError):
                    continue

    async def _save_stats(self, *, force: bool = False) -> None:
        if not (self._stats_dirty or force):
            return
        self._stats_dirty = False
        try:
            await self.put_kv_data(_STATS_KV_KEY, dict(self._stats))
        except Exception as exc:
            logger.debug("[%s] 保存统计失败：%s", PLUGIN_ID, exc)

    def _bump(self, key: str, amount: int = 1) -> None:
        self._stats[key] = self._stats.get(key, 0) + amount
        self._stats_dirty = True

    def _format_stats(self) -> str:
        stats = self._stats
        total = stats.get("searches", 0)
        hit_rate = (stats.get("hits", 0) / total * 100) if total else 0.0
        return "\n".join(
            [
                "📊 搜本子 · 运行统计",
                "",
                f"· 累计搜索：{total} 次",
                f"· 成功识别：{stats.get('hits', 0)} 次（命中率 {hit_rate:.1f}%）",
                f"· 没有结果：{stats.get('misses', 0)} 次",
                f"· 请求失败：{stats.get('failures', 0)} 次",
                f"· 命中缓存：{stats.get('cache_hits', 0)} 次",
                f"· LLM 工具调用：{stats.get('tool_calls', 0)} 次",
                "",
                f"默认模式：{'严格' if self.strict_mode_default else '普通'}"
                f"　缓存：{'开' if self.cache_enabled else '关'}"
                f"　并发上限：{self.max_concurrency}",
                f"访问链路：{self._describe_route()}",
            ]
        )

    # ------------------------------------------------------------- 权限 / 限流

    @staticmethod
    def _session_tokens(event: AstrMessageEvent) -> set[str]:
        tokens: set[str] = set()
        origin = getattr(event, "unified_msg_origin", "") or ""
        if origin:
            tokens.add(str(origin))
        try:
            group_id = event.get_group_id()
            if group_id:
                tokens.add(str(group_id))
        except Exception:
            pass
        try:
            sender_id = event.get_sender_id()
            if sender_id:
                tokens.add(str(sender_id))
        except Exception:
            pass
        return tokens

    def _permission_error(self, event: AstrMessageEvent | None) -> str:
        """返回拒绝原因，空串表示允许。"""
        if event is None:
            return ""

        is_private = False
        try:
            is_private = not event.get_group_id()
        except Exception:
            pass

        if self.private_only and not is_private:
            return "该功能仅允许在私聊中使用。"

        tokens = self._session_tokens(event)
        if self.blocked_sessions and tokens & set(self.blocked_sessions):
            return "当前会话已被管理员禁止使用搜本子功能。"
        if self.allowed_sessions and not (tokens & set(self.allowed_sessions)):
            return "当前会话未被列入搜本子白名单。"
        return ""

    def _cooldown_left(self, event: AstrMessageEvent | None) -> float:
        if self.cooldown_seconds <= 0 or event is None:
            return 0.0
        try:
            user_id = str(event.get_sender_id() or "")
        except Exception:
            return 0.0
        if not user_id:
            return 0.0
        left = self.cooldown_seconds - (
            time.monotonic() - self._cooldowns.get(user_id, 0.0)
        )
        return left if left > 0 else 0.0

    def _mark_cooldown(self, event: AstrMessageEvent | None) -> None:
        if event is None or self.cooldown_seconds <= 0:
            return
        try:
            user_id = str(event.get_sender_id() or "")
        except Exception:
            return
        if user_id:
            self._cooldowns[user_id] = time.monotonic()

    # ------------------------------------------------------------- 图片提取

    @staticmethod
    def _image_reference(component: Any) -> str:
        """从一个 Image 组件里取出可用的引用（URL / 本地路径 / base64）。"""
        for attr in ("url", "file"):
            value = read_str(getattr(component, attr, ""))
            if value:
                return value
        return ""

    async def _resolve_component(self, component: Any) -> str:
        """优先让 AstrBot 把图片落到本地，失败再退回原始 http(s) 链接。"""
        converter = getattr(component, "convert_to_file_path", None)
        if callable(converter):
            try:
                path = read_str(await converter())
                if path:
                    return path
            except Exception as exc:
                logger.debug("[%s] 图片转本地文件失败：%s", PLUGIN_ID, exc)

        reference = self._image_reference(component)
        if reference.startswith(("http://", "https://", "file://")):
            return reference
        if reference and local_path_from_reference(reference):
            return reference
        return ""

    async def extract_image_from_event(self, event: AstrMessageEvent) -> str:
        """
        按优先级找出这条消息想搜的图：
        1. 当前消息里的图片
        2. 被引用（回复）消息里的图片
        3. 平台原始包里的附件（Telegram / Discord / 微信）
        """
        chain = list(getattr(event.message_obj, "message", None) or [])

        for component in chain:
            if isinstance(component, MsgImage):
                reference = await self._resolve_component(component)
                if reference:
                    return reference

        for component in chain:
            if not isinstance(component, Reply):
                continue
            for sub in list(getattr(component, "chain", None) or []):
                if isinstance(sub, MsgImage):
                    reference = await self._resolve_component(sub)
                    if reference:
                        return reference

        raw = getattr(event.message_obj, "raw_message", None)

        # Telegram / Discord 之类把附件塞在 raw_message.attachments
        for attachment in list(getattr(raw, "attachments", None) or []):
            url = read_str(getattr(attachment, "url", "")) or read_str(
                getattr(attachment, "proxy_url", "")
            )
            if url.startswith(("http://", "https://")):
                return url

        # 微信（gewechat / wechatpadpro）把图片放在 item_list，type == 2 为图片
        for item in list(getattr(raw, "item_list", None) or []):
            try:
                if int(getattr(item, "type", 0)) != 2:
                    continue
                query = read_str(getattr(item, "encrypted_query_param", ""))
            except Exception:
                continue
            if query:
                return (
                    "https://novac2c.cdn.weixin.qq.com/c2c/download"
                    f"?encrypted_query_param={query}"
                )

        return ""

    def _event_has_image_reference(self, event: AstrMessageEvent) -> bool:
        """轻量判断（不下载、不落盘），用于自动搜索与提示注入。"""
        chain = list(getattr(event.message_obj, "message", None) or [])
        for component in chain:
            if isinstance(component, MsgImage):
                return True
            if isinstance(component, Reply):
                for sub in list(getattr(component, "chain", None) or []):
                    if isinstance(sub, MsgImage):
                        return True

        raw = getattr(event.message_obj, "raw_message", None)
        if list(getattr(raw, "attachments", None) or []):
            return True
        for item in list(getattr(raw, "item_list", None) or []):
            try:
                if int(getattr(item, "type", 0)) == 2:
                    return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------- 结果缓存

    @staticmethod
    def _cache_key(digest: str, strict: bool) -> str:
        return f"{digest}:{'strict' if strict else 'normal'}"

    def _cache_expired(self, stored_at: float) -> bool:
        if self.cache_ttl_hours <= 0:
            return False
        return (time.time() - stored_at) > self.cache_ttl_hours * 3600

    async def _cache_lookup(
        self, digest: str, strict: bool
    ) -> SoutubotSearchResult | None:
        if not self.cache_enabled:
            return None
        key = self._cache_key(digest, strict)

        entry = self._memory_cache.get(key)
        if entry is not None:
            stored_at, result = entry
            if not self._cache_expired(stored_at):
                return result
            self._memory_cache.pop(key, None)

        try:
            stored = await self.get_kv_data(_CACHE_KV_PREFIX + key, None)
        except Exception:
            return None
        if not isinstance(stored, dict):
            return None
        payload = stored.get("payload")
        if not isinstance(payload, dict):
            return None
        try:
            stored_at = float(stored.get("_stored_at", 0.0))
        except (TypeError, ValueError):
            return None
        if self._cache_expired(stored_at):
            return None
        try:
            result = SoutubotSearchResult.from_api(payload)
        except Exception:
            return None
        self._memory_cache[key] = (stored_at, result)
        return result

    async def _cache_store(
        self, digest: str, strict: bool, result: SoutubotSearchResult
    ) -> None:
        if not self.cache_enabled:
            return
        key = self._cache_key(digest, strict)
        now = time.time()

        self._memory_cache[key] = (now, result)
        while len(self._memory_cache) > _MEMORY_CACHE_LIMIT:
            oldest = min(
                self._memory_cache, key=lambda k: self._memory_cache[k][0]
            )
            self._memory_cache.pop(oldest, None)

        try:
            await self.put_kv_data(
                _CACHE_KV_PREFIX + key,
                {"_stored_at": now, "payload": result.to_cache()},
            )
        except Exception as exc:
            logger.debug("[%s] 写入结果缓存失败：%s", PLUGIN_ID, exc)

    # ------------------------------------------------------------- 核心搜索

    async def _search(
        self, image_reference: str, *, strict: bool
    ) -> tuple[SoutubotSearchResult, bool]:
        """下载 → 压缩 → 查缓存 → 请求 soutubot。返回 (结果, 是否来自缓存)。"""
        session = await self._ensure_session()
        raw = await fetch_image_bytes(
            session,
            image_reference,
            proxy=self.proxy,
            timeout=float(self.request_timeout),
        )
        image, filename, content_type = prepare_image(raw)
        digest = sha256_hex(image)

        cached = await self._cache_lookup(digest, strict)
        if cached is not None:
            self._bump("cache_hits")
            return cached, True

        client = await self._get_client()
        async with self._semaphore:
            result = await client.search(
                image,
                factor=FACTOR_STRICT if strict else FACTOR_NORMAL,
                filename=filename,
                content_type=content_type,
            )

        await self._cache_store(digest, strict, result)
        return result, False

    def _record_outcome(self, result: SoutubotSearchResult) -> None:
        self._bump("searches")
        usable = dedupe_matches(result.matches, min_similarity=self.min_similarity)
        self._bump("hits" if usable else "misses")

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        if isinstance(exc, ImageTooLargeError):
            return "❌ 图片太大了，请换一张小一点的图（建议 10MB 以内）。"
        if isinstance(exc, SoutubotBlockedError):
            # 403 属于「请求没进到应用层」，文案里已经带了原因与处置建议
            logger.warning(
                "[%s] 被拦截（%s）：%s",
                PLUGIN_ID,
                exc.kind,
                format_diagnostics(exc.detail),
            )
            return f"❌ {exc}"
        if isinstance(exc, SoutubotAuthError):
            return (
                "❌ 搜图Bot酱拒绝了这次请求（鉴权失败）。\n"
                "常见原因是本机系统时间不准，请校准时间后重试。"
            )
        if isinstance(exc, SoutubotRateLimitError):
            return "❌ 请求太频繁，被搜图Bot酱限流了，请稍后再试。"
        if isinstance(exc, SoutubotUpstreamError):
            return f"❌ 搜图Bot酱暂时出错了：{exc}"
        if isinstance(exc, SoutubotNetworkError):
            return "❌ 连不上搜图Bot酱，请检查网络或代理设置。"
        if isinstance(exc, SoutubotError):
            return f"❌ 搜索失败：{exc}"
        if isinstance(exc, asyncio.TimeoutError):
            return "❌ 搜索超时了，请稍后再试。"
        if isinstance(exc, aiohttp.ClientError):
            return "❌ 网络请求失败，请检查网络或代理设置。"
        if isinstance(exc, ValueError):
            return f"❌ 图片处理失败：{exc}"
        return f"❌ 出现未预期的错误：{exc}"

    # ------------------------------------------------------------- 消息发送

    async def _recall_later(
        self, event: AstrMessageEvent, message_id: Any, delay: int
    ) -> None:
        try:
            await asyncio.sleep(delay)
            await event.bot.call_action("delete_msg", message_id=int(message_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[%s] 撤回消息失败：%s", PLUGIN_ID, exc)

    async def _send_text(self, event: AstrMessageEvent, text: str) -> None:
        """按配置选择：定时撤回 / 合并转发 / 普通消息。任一层失败都会回退。"""
        platform = ""
        try:
            platform = read_str(event.get_platform_name())
        except Exception:
            platform = ""

        if self.recall_after_seconds > 0 and platform == "aiocqhttp":
            try:
                sent = await event.bot.send(event.message_obj.raw_message, text)
                message_id = (sent or {}).get("message_id")
                if message_id is not None:
                    asyncio.create_task(
                        self._recall_later(
                            event, message_id, self.recall_after_seconds
                        )
                    )
                return
            except Exception as exc:
                logger.debug("[%s] 定时撤回发送失败，回退普通消息：%s", PLUGIN_ID, exc)

        if self.use_forward_message and platform == "aiocqhttp":
            try:
                uin = read_str(event.get_self_id()) or "10000"
                node = Comp.Node(
                    content=[Comp.Plain(text)], name=MAIN_COMMAND, uin=uin
                )
                await event.send(event.chain_result([Comp.Nodes([node])]))
                return
            except Exception as exc:
                logger.debug("[%s] 合并转发失败，回退普通消息：%s", PLUGIN_ID, exc)

        await event.send(event.plain_result(text))

    def _describe_route(self) -> str:
        """一行文本描述当前请求链路，供 `搜本子 统计` 展示。"""
        if self._client is not None:
            route = self._client.describe_transport()
        elif self.reverse_proxy_url:
            route = f"aiohttp / 反代 {self.reverse_proxy_url}"
        else:
            route = "aiohttp / 直连 soutubot.moe"
        if self.tls_impersonate != "off" and not curl_cffi_available():
            route += "（未安装 curl_cffi，无法伪装指纹）"
        return route

    def _proxy_image_url(self, url: str) -> str:
        """预览图可选地也走反代，避免图片域名同样被 Cloudflare 拦。"""
        raw = read_str(url, "")
        if not raw or not self.reverse_proxy_url or not self.reverse_proxy_images:
            return raw
        query = urlencode({"url": raw})
        if self.reverse_proxy_token:
            query += "&" + urlencode({"token": self.reverse_proxy_token})
        return f"{self.reverse_proxy_url}/img?{query}"

    async def _send_previews(
        self, event: AstrMessageEvent, result: SoutubotSearchResult
    ) -> None:
        """可选地把候选的预览图发出来（默认关闭，因为内容通常是 NSFW）。"""
        if not self.send_preview_image or self.max_preview_images <= 0:
            return
        matches = dedupe_matches(result.matches, min_similarity=self.min_similarity)
        urls = [
            self._proxy_image_url(m.preview_image_url)
            for m in matches[: self.max_preview_images]
            if read_str(getattr(m, "preview_image_url", ""))
        ]
        if not urls:
            return
        try:
            await event.send(
                event.chain_result([Comp.Image.fromURL(url) for url in urls])
            )
        except Exception as exc:
            logger.debug("[%s] 发送预览图失败：%s", PLUGIN_ID, exc)

    async def _report(
        self,
        event: AstrMessageEvent,
        result: SoutubotSearchResult,
        cached: bool,
    ) -> None:
        text = format_plain_report(
            result,
            mirrors=self.mirrors,
            max_results=self.max_results,
            min_similarity=self.min_similarity,
            show_language=self.show_language,
            include_result_link=self.show_urls,
            base_url=self.base_url,
        )
        if cached:
            text += "\n♻️ 本次结果来自本地缓存。"
        await self._send_text(event, text)
        await self._send_previews(event, result)

    # ------------------------------------------------------------- 命令解析

    def _command_args(self, event: AstrMessageEvent) -> str:
        """把「/搜本子 严格」这样的消息剥成参数串「严格」。"""
        text = read_str(getattr(event, "message_str", "")).strip()
        for prefix in WAKE_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
                break
        lowered = text.lower()
        for name in (MAIN_COMMAND, *COMMAND_ALIASES):
            if lowered.startswith(name.lower()):
                return text[len(name) :].strip()
        return text

    def _looks_like_command(self, event: AstrMessageEvent) -> bool:
        """on_message 用它跳过命令消息，避免同一条消息被处理两次。"""
        text = read_str(getattr(event, "message_str", "")).strip()
        for prefix in WAKE_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
                break
        lowered = text.lower()
        return any(
            lowered.startswith(name.lower())
            for name in (MAIN_COMMAND, *COMMAND_ALIASES)
        )

    @filter.command(MAIN_COMMAND, alias=set(COMMAND_ALIASES))
    async def soutubot_command(
        self, event: AstrMessageEvent, args: str | None = None
    ):
        raw_args = read_str(args) or self._command_args(event)
        parts = raw_args.split()
        head = parts[0].lower() if parts else ""

        # 这三个子命令是纯信息查询，不做权限与冷却限制
        if head in HELP_WORDS:
            await event.send(event.plain_result(HELP_TEXT))
            return
        if head in MIRROR_WORDS:
            await event.send(
                event.plain_result(
                    describe_mirrors()
                    + "\n\n当前设置："
                    + f"nhentai → {self.mirrors.get('nhentai')}"
                    + f"　ehentai → {self.mirrors.get('ehentai')}"
                )
            )
            return
        if head in STATS_WORDS:
            await event.send(event.plain_result(self._format_stats()))
            return

        denied = self._permission_error(event)
        if denied:
            await event.send(event.plain_result(denied))
            return

        if head in RESULT_WORDS:
            await self._handle_result_lookup(
                event, parts[1] if len(parts) > 1 else ""
            )
            return

        strict = self.strict_mode_default
        if head in STRICT_WORDS:
            strict = True
        elif head in NORMAL_WORDS:
            strict = False

        left = self._cooldown_left(event)
        if left > 0:
            await event.send(
                event.plain_result(f"⏳ 冷却中，请 {left:.0f} 秒后再试。")
            )
            return

        reference = await self.extract_image_from_event(event)
        if reference:
            await self._run_search_and_reply(event, reference, strict=strict)
        else:
            await self._start_waiting(event, strict)

    async def _handle_result_lookup(
        self, event: AstrMessageEvent, result_id: str
    ) -> None:
        result_id = read_str(result_id).strip()
        if not result_id.isdigit():
            await event.send(
                event.plain_result(
                    "请给出一个数字结果 ID，例如：搜本子 结果 2026082616234698"
                )
            )
            return
        try:
            client = await self._get_client()
            result = await client.fetch_result(result_id)
        except Exception as exc:
            logger.warning("[%s] 复查结果 %s 失败：%s", PLUGIN_ID, result_id, exc)
            await event.send(event.plain_result(self._friendly_error(exc)))
            return
        await self._report(event, result, cached=False)

    async def _run_search_and_reply(
        self, event: AstrMessageEvent, reference: str, *, strict: bool
    ) -> None:
        self._mark_cooldown(event)
        try:
            result, cached = await self._search(reference, strict=strict)
        except Exception as exc:
            self._bump("failures")
            await self._save_stats()
            logger.warning("[%s] 搜索失败：%s", PLUGIN_ID, exc)
            await event.send(event.plain_result(self._friendly_error(exc)))
            return
        self._record_outcome(result)
        await self._save_stats()
        await self._report(event, result, cached)

    # ------------------------------------------------------------- 等待补图

    async def _start_waiting(self, event: AstrMessageEvent, strict: bool) -> None:
        try:
            user_id = read_str(event.get_sender_id())
        except Exception:
            user_id = ""
        if not user_id:
            await event.send(
                event.plain_result("请把图片和「搜本子」一起发送。")
            )
            return

        old = self.timeout_tasks.pop(user_id, None)
        if old is not None:
            old.cancel()

        self.waiting_sessions[user_id] = {
            "strict": strict,
            "timestamp": time.monotonic(),
            "origin": read_str(getattr(event, "unified_msg_origin", "")),
        }
        self.timeout_tasks[user_id] = asyncio.create_task(
            self._waiting_timeout(event, user_id)
        )
        if self.prompt_send_image:
            await event.send(event.plain_result(self.prompt_send_image))

    async def _waiting_timeout(
        self, event: AstrMessageEvent, user_id: str
    ) -> None:
        try:
            await asyncio.sleep(self.wait_timeout_seconds)
        except asyncio.CancelledError:
            return
        if self.waiting_sessions.pop(user_id, None) is None:
            return
        self.timeout_tasks.pop(user_id, None)
        if self.prompt_timeout:
            try:
                await event.send(event.plain_result(self.prompt_timeout))
            except Exception as exc:
                logger.debug("[%s] 发送超时提示失败：%s", PLUGIN_ID, exc)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if self._looks_like_command(event):
            return

        try:
            user_id = read_str(event.get_sender_id())
        except Exception:
            return

        waiting = self.waiting_sessions.get(user_id) if user_id else None
        if waiting is not None:
            reference = await self.extract_image_from_event(event)
            if not reference:
                return
            self.waiting_sessions.pop(user_id, None)
            task = self.timeout_tasks.pop(user_id, None)
            if task is not None:
                task.cancel()
            await self._run_search_and_reply(
                event, reference, strict=bool(waiting.get("strict"))
            )
            event.stop_event()
            return

        if not self.auto_search_on_image:
            return
        if self._permission_error(event):
            return
        if self._cooldown_left(event) > 0:
            return
        if not self._event_has_image_reference(event):
            return
        reference = await self.extract_image_from_event(event)
        if not reference:
            return
        await self._run_search_and_reply(
            event, reference, strict=self.strict_mode_default
        )

    # ------------------------------------------------------------- LLM 工具

    async def search_for_tool(
        self,
        *,
        event: AstrMessageEvent | None = None,
        image_url: str = "",
        strict: Any = None,
        max_results: Any = None,
    ) -> str:
        """被 LLM function tool 调用的入口，返回给模型阅读的纯文本摘要。"""
        self._bump("tool_calls")

        denied = self._permission_error(event)
        if denied:
            return f"无法执行搜索：{denied}"

        left = self._cooldown_left(event)
        if left > 0:
            return f"无法执行搜索：冷却中，还需等待约 {left:.0f} 秒。"

        reference = read_str(image_url).strip()
        if not reference and event is not None:
            reference = await self.extract_image_from_event(event)
        if not reference:
            return (
                "没有找到可搜索的图片。请让用户直接发送本子截图，"
                "或引用一条带图片的消息后再提问。不要凭空猜测书名。"
            )

        use_strict = (
            self.strict_mode_default if strict is None else read_bool(strict, False)
        )
        limit = read_int(max_results, self.llm_tool_max_results, minimum=1, maximum=10)

        self._mark_cooldown(event)
        try:
            result, cached = await self._search(reference, strict=use_strict)
        except Exception as exc:
            self._bump("failures")
            await self._save_stats()
            logger.warning("[%s] LLM 工具搜索失败：%s", PLUGIN_ID, exc)
            return (
                f"搜索失败：{exc}。请如实告知用户这次检索没有成功，"
                "不要编造任何书名或链接。"
            )

        self._record_outcome(result)
        await self._save_stats()

        summary = format_llm_summary(
            result,
            mirrors=self.mirrors,
            max_results=limit,
            min_similarity=self.min_similarity,
            base_url=self.base_url if self.show_urls else "",
            include_urls=self.llm_tool_include_urls,
        )
        if cached:
            summary += "\n（本次结果来自本地缓存。）"

        if event is not None:
            await self._send_previews(event, result)
        return summary

    def _message_requests_tool(self, event: AstrMessageEvent) -> bool:
        text = read_str(getattr(event, "message_str", "")).lower()
        if not text:
            return False
        return any(
            keyword.lower() in text
            for keyword in self.tool_request_keywords
            if keyword
        )

    @filter.on_llm_request(priority=-5)
    async def inject_soutubot_tool_hint(
        self, event: AstrMessageEvent, request: ProviderRequest
    ):
        """
        当这轮对话看起来是在问「这是什么本子」时，临时提示模型可以用搜本子工具。
        提示是 temp 的，不会污染长期对话历史。
        """
        if not (self.llm_tool_enabled and self.inject_llm_tool_hint):
            return
        has_image = False
        try:
            has_image = self._event_has_image_reference(event)
        except Exception:
            has_image = False
        if not (has_image or self._message_requests_tool(event)):
            return

        hint = (
            f"提示：你可以调用 {TOOL_NAME} 工具，用搜图Bot酱（soutubot.moe）"
            "对用户发送或引用的图片做本子以图搜书。"
            "调用时 image_url 留空即可，插件会自动取当前消息里的图片。"
            "工具返回的书名、相似度与链接来自图像检索，"
            "相似度不高时必须明确告知用户这只是可能的结果，严禁编造。"
        )
        try:
            request.extra_user_content_parts.append(
                TextPart(text=hint).mark_as_temp()
            )
        except Exception:
            try:
                request.system_prompt = (
                    read_str(getattr(request, "system_prompt", "")) + "\n\n" + hint
                )
            except Exception as exc:
                logger.debug("[%s] 注入工具提示失败：%s", PLUGIN_ID, exc)
