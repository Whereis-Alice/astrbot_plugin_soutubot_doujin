"""soutubot.moe 搜索响应的数据模型。

真实响应形态（已实测）::

    {
      "id": "2026082615542720",
      "factor": 1.4,
      "imageUrl": "https://img.../xxx.webp",
      "searchOption": "api 1.4 Liner 64",
      "executionTime": 1.04,
      "data": [
        {
          "source": "nhentai",              # nhentai | ehentai | panda
          "page": 10,                        # 命中页码，panda 可能为 0
          "title": "BugBug 2024-08 [Digital]",
          "language": "jp",                  # ISO 区域码
          "pagePath": "/g/518943/10",        # panda 为 null
          "subjectPath": "/g/518943",
          "previewImageUrl": "https://img.../0010.webp",
          "similarity": 67.45
        }
      ]
    }

不同来源的路径形态::

    nhentai  pagePath=/g/{gid}/{page}          subjectPath=/g/{gid}
    ehentai  pagePath=/s/{hash}/{gid}-{page}   subjectPath=/g/{gid}/{token}
    panda    pagePath=null                     subjectPath=/archive/{id}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .mirrors import language_label, mirror_host, source_label

_GALLERY_ID_PATTERN = re.compile(r"/(?:g|archive)/(\d+)")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


@dataclass(slots=True)
class SoutubotMatch:
    """单条命中结果。"""

    source: str = ""
    page: int = 0
    title: str = ""
    language: str = ""
    page_path: str = ""
    subject_path: str = ""
    preview_image_url: str = ""
    similarity: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "SoutubotMatch":
        payload = payload or {}
        return cls(
            source=_as_text(payload.get("source")).lower(),
            page=_as_int(payload.get("page")),
            title=_as_text(payload.get("title")),
            language=_as_text(payload.get("language")).lower(),
            page_path=_as_text(payload.get("pagePath")),
            subject_path=_as_text(payload.get("subjectPath")),
            preview_image_url=_as_text(payload.get("previewImageUrl")),
            similarity=_as_float(payload.get("similarity")),
            raw=dict(payload),
        )

    # ---- 展示辅助 ----

    @property
    def source_name(self) -> str:
        return source_label(self.source)

    @property
    def language_name(self) -> str:
        return language_label(self.language)

    @property
    def gallery_id(self) -> str:
        """画廊 ID，可用于 nhentai 的 `/g/{id}` 或 e-hentai 的 gid。"""
        match = _GALLERY_ID_PATTERN.search(self.subject_path or self.page_path)
        return match.group(1) if match else ""

    @property
    def dedupe_key(self) -> tuple[str, str]:
        """同一本书的去重键。"""
        return (self.source, self.subject_path or self.page_path)

    def subject_url(self, mirror_choice: object = 0) -> str:
        """本子（画廊）主页地址。"""
        host = mirror_host(self.source, mirror_choice)
        if not host or not self.subject_path:
            return ""
        return f"https://{host}{self.subject_path}"

    def page_url(self, mirror_choice: object = 0) -> str:
        """命中页地址；panda 等没有单页地址时回退到画廊地址。"""
        host = mirror_host(self.source, mirror_choice)
        if host and self.page_path:
            return f"https://{host}{self.page_path}"
        return self.subject_url(mirror_choice)

    def best_url(self, mirror_choice: object = 0) -> str:
        return self.page_url(mirror_choice) or self.subject_url(mirror_choice)


@dataclass(slots=True)
class SoutubotSearchResult:
    """一次搜索的完整结果。"""

    result_id: str = ""
    factor: float = 0.0
    image_url: str = ""
    search_option: str = ""
    execution_time: float = 0.0
    matches: list[SoutubotMatch] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "SoutubotSearchResult":
        payload = payload or {}
        rows = payload.get("data") or []
        return cls(
            result_id=_as_text(payload.get("id")),
            factor=_as_float(payload.get("factor")),
            image_url=_as_text(payload.get("imageUrl")),
            search_option=_as_text(payload.get("searchOption")),
            execution_time=_as_float(payload.get("executionTime")),
            matches=[
                SoutubotMatch.from_api(row) for row in rows if isinstance(row, dict)
            ],
        )

    def to_cache(self) -> dict[str, Any]:
        """序列化为可写入 KV 的纯 dict（保持 API 原始字段名）。"""
        return {
            "id": self.result_id,
            "factor": self.factor,
            "imageUrl": self.image_url,
            "searchOption": self.search_option,
            "executionTime": self.execution_time,
            "data": [dict(match.raw) for match in self.matches],
        }

    @property
    def strict(self) -> bool:
        """factor > 1.2 即前端所说的“严格模式”。"""
        return self.factor > 1.2

    @property
    def best_similarity(self) -> float:
        return max((m.similarity for m in self.matches), default=0.0)

    def result_page_url(self, base_url: str) -> str:
        if not self.result_id:
            return ""
        return f"{base_url.rstrip('/')}/results/{self.result_id}"
