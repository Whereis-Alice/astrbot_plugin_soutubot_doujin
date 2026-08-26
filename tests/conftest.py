"""测试公共设置：把插件根目录加入 sys.path，并提供真实形态的响应 fixture。

所有测试均为离线测试，不会发起任何真实网络请求。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------- 真实响应片段

# nhentai：pagePath=/g/{gid}/{page}，subjectPath=/g/{gid}
NHENTAI_ROW: dict[str, Any] = {
    "source": "nhentai",
    "page": 10,
    "title": "BugBug 2024-08 [Digital]",
    "language": "jp",
    "pagePath": "/g/518943/10",
    "subjectPath": "/g/518943",
    "previewImageUrl": "https://img.soutubot.moe/nhentai/518943/0010.webp",
    "similarity": 67.45,
}

# ehentai：pagePath=/s/{hash}/{gid}-{page}，subjectPath=/g/{gid}/{token}
EHENTAI_ROW: dict[str, Any] = {
    "source": "ehentai",
    "page": 7,
    "title": "(C99) [サークル (作者)] タイトル [中国翻訳]",
    "language": "cn",
    "pagePath": "/s/9f2a1b3c4d/2718281-7",
    "subjectPath": "/g/2718281/1a2b3c4d5e",
    "previewImageUrl": "https://img.soutubot.moe/ehentai/2718281/0007.webp",
    "similarity": 41.02,
}

# panda：pagePath 为 null，page 常为 0，subjectPath=/archive/{id}
PANDA_ROW: dict[str, Any] = {
    "source": "panda",
    "page": 0,
    "title": "Some Doujin Title [DL版]",
    "language": "tw",
    "pagePath": None,
    "subjectPath": "/archive/31415",
    "previewImageUrl": "https://img.soutubot.moe/panda/31415/cover.webp",
    "similarity": 33.8,
}


def make_payload(
    rows: list[dict[str, Any]] | None = None,
    *,
    result_id: str = "2026082615542720",
    factor: float = 1.4,
    execution_time: float = 1.04,
) -> dict[str, Any]:
    """构造一份与真实 API 同形态的响应。"""
    if rows is None:
        rows = [NHENTAI_ROW, EHENTAI_ROW, PANDA_ROW]
    return {
        "id": result_id,
        "factor": factor,
        "imageUrl": "https://img.soutubot.moe/upload/x.webp",
        "searchOption": f"api {factor} Liner 64",
        "executionTime": execution_time,
        "data": copy.deepcopy(rows),
    }


def make_row(base: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """基于某个真实来源行做局部覆盖。"""
    row = copy.deepcopy(base)
    row.update(overrides)
    return row


@pytest.fixture
def nhentai_row() -> dict[str, Any]:
    return copy.deepcopy(NHENTAI_ROW)


@pytest.fixture
def ehentai_row() -> dict[str, Any]:
    return copy.deepcopy(EHENTAI_ROW)


@pytest.fixture
def panda_row() -> dict[str, Any]:
    return copy.deepcopy(PANDA_ROW)


@pytest.fixture
def full_payload() -> dict[str, Any]:
    return make_payload()
