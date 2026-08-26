"""把搜索结果整理成人类可读文本 / LLM 可消费摘要。

相似度阈值沿用 soutubot.moe 前端的判定：

- ``similarity < 28``：前端归入“低相似度”折叠区，基本可视为噪声
- ``similarity >= 45``（普通模式）或 ``>= 35``（严格模式）：前端显示为绿色，可信度高
- 两者之间：前端显示为黄色，仅供参考
"""

from __future__ import annotations

import re
from enum import Enum

from .mirrors import mirror_name
from .models import SoutubotMatch, SoutubotSearchResult

# 前端 results.filter(similarity >= 28)
MIN_SIMILARITY = 28.0
# 前端 lowSimilarityScore = strictMode ? 35 : 45
HIGH_THRESHOLD_STRICT = 35.0
HIGH_THRESHOLD_NORMAL = 45.0


class ConfidenceTier(str, Enum):
    """命中结果的可信度档位。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


_TIER_LABELS = {
    ConfidenceTier.HIGH: "高可信",
    ConfidenceTier.MEDIUM: "仅供参考",
    ConfidenceTier.LOW: "低相似度",
}

_TIER_ICONS = {
    ConfidenceTier.HIGH: "🟢",
    ConfidenceTier.MEDIUM: "🟡",
    ConfidenceTier.LOW: "⚪",
}


def confidence_tier(similarity: float, *, strict: bool = False) -> ConfidenceTier:
    """按相似度给出可信度档位。"""
    try:
        score = float(similarity)
    except (TypeError, ValueError):
        score = 0.0
    if score < MIN_SIMILARITY:
        return ConfidenceTier.LOW
    threshold = HIGH_THRESHOLD_STRICT if strict else HIGH_THRESHOLD_NORMAL
    return ConfidenceTier.HIGH if score >= threshold else ConfidenceTier.MEDIUM


def tier_label(tier: ConfidenceTier) -> str:
    return _TIER_LABELS.get(tier, "未知")


def tier_icon(tier: ConfidenceTier) -> str:
    return _TIER_ICONS.get(tier, "•")


# ------------------------------------------------------------ 标题清洗

# 方括号里出现这些词，基本都是版本/翻译/汉化组等噪声，展示时可省略
_NOISE_KEYWORDS = (
    "digital",
    "dl版",
    "dl版",
    "無修正",
    "无修正",
    "decensored",
    "中国翻訳",
    "中國翻訳",
    "中国翻译",
    "汉化",
    "漢化",
    "翻訳",
    "翻译",
    "english",
    "korean",
    "русский",
    "reprint",
    "同人誌",
)

_BRACKET_PATTERN = re.compile(r"[\[\{【]([^\[\]\{\}【】]*)[\]\}】]")
_SPACE_PATTERN = re.compile(r"\s{2,}")


def _is_noise(chunk: str) -> bool:
    lowered = chunk.strip().lower()
    if not lowered:
        return True
    return any(keyword in lowered for keyword in _NOISE_KEYWORDS)


def clean_title(title: str, *, max_length: int = 120) -> str:
    """去掉标题里的版本/汉化组噪声方括号，并压缩空白。

    只删除“明显是噪声”的方括号，作者名等信息会被保留，
    因为它对辨认本子很关键。
    """
    text = str(title or "").strip()
    if not text:
        return ""

    cleaned = _BRACKET_PATTERN.sub(
        lambda match: "" if _is_noise(match.group(1)) else match.group(0),
        text,
    )
    cleaned = _SPACE_PATTERN.sub(" ", cleaned).strip(" -_·|")
    cleaned = cleaned or text

    if max_length > 0 and len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 1].rstrip() + "…"
    return cleaned


# ------------------------------------------------------------ 结果整理


def dedupe_matches(
    matches: list[SoutubotMatch],
    *,
    min_similarity: float = 0.0,
) -> list[SoutubotMatch]:
    """同一本书只保留相似度最高的一条，并按相似度降序排列。"""
    best: dict[tuple[str, str], SoutubotMatch] = {}
    for match in matches or []:
        if match.similarity < min_similarity:
            continue
        key = match.dedupe_key
        current = best.get(key)
        if current is None or match.similarity > current.similarity:
            best[key] = match
    return sorted(best.values(), key=lambda m: m.similarity, reverse=True)


def _mirror_choice(mirrors: dict[str, object] | None, source: str) -> object:
    if not mirrors:
        return 0
    return mirrors.get(source, 0)


# ------------------------------------------------------------ 文本渲染


def format_plain_report(
    result: SoutubotSearchResult,
    *,
    mirrors: dict[str, object] | None = None,
    max_results: int = 5,
    min_similarity: float = MIN_SIMILARITY,
    show_language: bool = True,
    include_result_link: bool = True,
    base_url: str = "",
) -> str:
    """渲染给人看的搜索结果。"""
    strict = result.strict
    mode = "严格" if strict else "普通"
    kept = dedupe_matches(result.matches, min_similarity=min_similarity)

    header = f"🔍 搜图Bot酱 · {mode}模式"
    if result.execution_time:
        header += f" · 耗时 {result.execution_time:.2f}s"

    if not kept:
        fallback = dedupe_matches(result.matches)
        lines = [header, "", "😶 没有找到相似度足够高的本子。"]
        if fallback:
            top = fallback[0]
            lines.append(
                f"最接近的一条只有 {top.similarity:.2f}% 相似度，"
                "低于可信阈值，已忽略。"
            )
        lines.append("可以试试：换一张更清晰、更完整的单页图，或使用严格模式。")
        if include_result_link and base_url:
            link = result.result_page_url(base_url)
            if link:
                lines.append(f"完整结果：{link}")
        return "\n".join(lines)

    limit = max_results if max_results > 0 else len(kept)
    shown = kept[:limit]

    lines = [header, ""]
    for index, match in enumerate(shown, start=1):
        tier = confidence_tier(match.similarity, strict=strict)
        title = clean_title(match.title) or "（无标题）"
        lines.append(f"{index}. {tier_icon(tier)} {title}")

        meta = [f"相似度 {match.similarity:.2f}%（{tier_label(tier)}）"]
        if show_language and match.language:
            meta.append(match.language_name)
        lines.append(f"   {' · '.join(meta)}")

        choice = _mirror_choice(mirrors, match.source)
        source_desc = match.source_name
        mirror = mirror_name(match.source, choice)
        if mirror and mirror not in source_desc:
            source_desc = f"{source_desc} / {mirror}"
        page_note = f"第 {match.page} 页" if match.page else "整本"
        lines.append(f"   来源：{source_desc} · {page_note}")

        url = match.best_url(choice)
        if url:
            lines.append(f"   {url}")
        lines.append("")

    if len(kept) > len(shown):
        lines.append(f"（另有 {len(kept) - len(shown)} 条较低相似度结果未显示）")

    if include_result_link and base_url:
        link = result.result_page_url(base_url)
        if link:
            lines.append(f"完整结果：{link}")

    top_tier = confidence_tier(shown[0].similarity, strict=strict)
    if top_tier is not ConfidenceTier.HIGH:
        lines.append("⚠️ 最高相似度偏低，结果可能不准，请自行核对封面。")

    return "\n".join(line for line in lines).strip()


def format_llm_summary(
    result: SoutubotSearchResult,
    *,
    mirrors: dict[str, object] | None = None,
    max_results: int = 5,
    min_similarity: float = MIN_SIMILARITY,
    base_url: str = "",
    include_urls: bool = True,
) -> str:
    """渲染给 LLM 读的紧凑摘要（明确标注可信度，避免模型过度自信）。"""
    strict = result.strict
    kept = dedupe_matches(result.matches, min_similarity=min_similarity)

    if not kept:
        fallback = dedupe_matches(result.matches)
        best = f"{fallback[0].similarity:.2f}%" if fallback else "无"
        return (
            "soutubot 搜索完成，但没有达到可信阈值的结果"
            f"（最高相似度 {best}，阈值 {min_similarity:.0f}%）。"
            "请告诉用户这次没能识别出本子，建议换一张更清晰、更有辨识度的单页图，"
            "不要凭猜测编造书名。"
        )

    limit = max_results if max_results > 0 else len(kept)
    shown = kept[:limit]

    lines = [
        "soutubot（搜图Bot酱）以图搜本子结果，"
        f"模式={'严格' if strict else '普通'}，共 {len(shown)} 条候选，"
        "按相似度从高到低："
    ]
    for index, match in enumerate(shown, start=1):
        tier = confidence_tier(match.similarity, strict=strict)
        parts = [
            f"{index}. 标题《{clean_title(match.title) or '未知'}》",
            f"相似度 {match.similarity:.2f}%（{tier_label(tier)}）",
            f"来源 {match.source_name}",
        ]
        if match.language:
            parts.append(f"语言 {match.language_name}")
        if match.page:
            parts.append(f"命中第 {match.page} 页")
        if include_urls:
            url = match.best_url(_mirror_choice(mirrors, match.source))
            if url:
                parts.append(f"链接 {url}")
        lines.append("，".join(parts))

    top_tier = confidence_tier(shown[0].similarity, strict=strict)
    if top_tier is ConfidenceTier.HIGH:
        lines.append(
            "第 1 条相似度较高，可以作为答案给出，但仍要提示用户自行核对。"
        )
    else:
        lines.append(
            "所有候选相似度都不高，回答时必须明确说明这只是可能的结果，不要断言。"
        )

    if base_url:
        link = result.result_page_url(base_url)
        if link:
            lines.append(f"完整结果页：{link}")

    lines.append("注意：这些结果来自图片检索，不是你的先验知识，不要额外编造作者或章节信息。")
    return "\n".join(lines)
