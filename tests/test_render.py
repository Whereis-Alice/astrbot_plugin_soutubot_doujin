"""render.py：可信度档位、标题清洗、去重与两种文本渲染。"""

from __future__ import annotations

import pytest
from conftest import EHENTAI_ROW, NHENTAI_ROW, PANDA_ROW, make_payload, make_row

from soutubot.models import SoutubotMatch, SoutubotSearchResult
from soutubot.render import (
    MIN_SIMILARITY,
    ConfidenceTier,
    clean_title,
    confidence_tier,
    dedupe_matches,
    format_llm_summary,
    format_plain_report,
    tier_icon,
    tier_label,
)


def match_of(source_row, **overrides) -> SoutubotMatch:
    return SoutubotMatch.from_api(make_row(source_row, **overrides))


# ---------------------------------------------------------------- confidence


def test_min_similarity_constant():
    assert MIN_SIMILARITY == 28.0


@pytest.mark.parametrize(
    ("score", "normal", "strict"),
    [
        (0.0, ConfidenceTier.LOW, ConfidenceTier.LOW),
        (27.99, ConfidenceTier.LOW, ConfidenceTier.LOW),
        (28.0, ConfidenceTier.MEDIUM, ConfidenceTier.MEDIUM),  # 阈值取等号
        (28.01, ConfidenceTier.MEDIUM, ConfidenceTier.MEDIUM),
        (34.99, ConfidenceTier.MEDIUM, ConfidenceTier.MEDIUM),
        (35.0, ConfidenceTier.MEDIUM, ConfidenceTier.HIGH),  # 严格模式在 35 转绿
        (44.99, ConfidenceTier.MEDIUM, ConfidenceTier.HIGH),
        (45.0, ConfidenceTier.HIGH, ConfidenceTier.HIGH),  # 普通模式在 45 转绿
        (99.9, ConfidenceTier.HIGH, ConfidenceTier.HIGH),
    ],
)
def test_confidence_tier_boundaries(score, normal, strict):
    assert confidence_tier(score) is normal
    assert confidence_tier(score, strict=False) is normal
    assert confidence_tier(score, strict=True) is strict


@pytest.mark.parametrize("bad", [None, "abc", object()])
def test_confidence_tier_invalid_input_is_low(bad):
    assert confidence_tier(bad) is ConfidenceTier.LOW


def test_confidence_tier_accepts_numeric_string():
    assert confidence_tier("50") is ConfidenceTier.HIGH


def test_tier_label_and_icon():
    assert tier_label(ConfidenceTier.HIGH) == "高可信"
    assert tier_label(ConfidenceTier.MEDIUM) == "仅供参考"
    assert tier_label(ConfidenceTier.LOW) == "低相似度"
    assert tier_icon(ConfidenceTier.HIGH) == "\U0001f7e2"
    assert tier_icon(ConfidenceTier.MEDIUM) == "\U0001f7e1"
    assert tier_icon(ConfidenceTier.LOW) == "\u26aa"
    # 三个档位的图标互不相同
    assert len({tier_icon(t) for t in ConfidenceTier}) == 3


# ---------------------------------------------------------------- clean_title


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BugBug 2024-08 [Digital]", "BugBug 2024-08"),
        ("Title [DL版]", "Title"),
        ("Title [中国翻訳]", "Title"),
        ("Title [中国翻译]", "Title"),
        ("Title [汉化]", "Title"),
        ("Title [English]", "Title"),
        ("Title [Decensored]", "Title"),
        ("Title [無修正]", "Title"),
        ("Title 【DL版】", "Title"),
        ("Title {Digital}", "Title"),
        ("Title [Digital] [中国翻訳] [DL版]", "Title"),
    ],
)
def test_clean_title_strips_noise_brackets(raw, expected):
    assert clean_title(raw) == expected


def test_clean_title_keeps_author_bracket():
    raw = "(C99) [サークル (作者)] タイトル [中国翻訳] [Digital]"
    cleaned = clean_title(raw)
    assert cleaned == "(C99) [サークル (作者)] タイトル"
    assert "[サークル (作者)]" in cleaned  # 作者/社团信息必须保留


def test_clean_title_keeps_non_noise_brackets():
    assert clean_title("[あいうえお] 本のタイトル") == "[あいうえお] 本のタイトル"


def test_clean_title_collapses_whitespace():
    assert clean_title("A    B     C") == "A B C"
    assert clean_title("  Title [Digital]   ") == "Title"


def test_clean_title_empty_inputs():
    assert clean_title("") == ""
    assert clean_title("   ") == ""
    assert clean_title(None) == ""


def test_clean_title_all_noise_falls_back_to_original():
    # 全部内容都是噪声时不能返回空串，否则展示上会丢信息
    assert clean_title("[Digital]") == "[Digital]"


def test_clean_title_truncates_to_max_length():
    cleaned = clean_title("A" * 300)
    assert len(cleaned) == 120
    assert cleaned.endswith("…")
    assert cleaned[:119] == "A" * 119


def test_clean_title_custom_max_length():
    assert clean_title("ABCDEFGHIJ", max_length=5) == "ABCD…"
    assert clean_title("ABCDE", max_length=5) == "ABCDE"  # 恰好等长不截断


def test_clean_title_max_length_zero_disables_truncation():
    long_title = "B" * 400
    assert clean_title(long_title, max_length=0) == long_title


def test_clean_title_strips_trailing_separators():
    assert clean_title("Title - [Digital]") == "Title"
    assert clean_title("Title | [DL版]") == "Title"


# ---------------------------------------------------------------- dedupe


def test_dedupe_keeps_highest_similarity_per_book():
    matches = [
        match_of(NHENTAI_ROW, page=3, pagePath="/g/518943/3", similarity=30.0),
        match_of(NHENTAI_ROW, page=10, pagePath="/g/518943/10", similarity=67.45),
        match_of(NHENTAI_ROW, page=5, pagePath="/g/518943/5", similarity=52.0),
    ]
    kept = dedupe_matches(matches)
    assert len(kept) == 1
    assert kept[0].similarity == pytest.approx(67.45)
    assert kept[0].page == 10


def test_dedupe_sorts_descending():
    matches = [
        match_of(PANDA_ROW, similarity=33.8),
        match_of(NHENTAI_ROW, similarity=67.45),
        match_of(EHENTAI_ROW, similarity=41.02),
    ]
    kept = dedupe_matches(matches)
    assert [m.source for m in kept] == ["nhentai", "ehentai", "panda"]
    scores = [m.similarity for m in kept]
    assert scores == sorted(scores, reverse=True)


def test_dedupe_does_not_merge_different_sources():
    matches = [
        match_of(NHENTAI_ROW, similarity=50.0),
        match_of(EHENTAI_ROW, similarity=49.0),
    ]
    assert len(dedupe_matches(matches)) == 2


def test_dedupe_min_similarity_is_inclusive():
    matches = [
        match_of(NHENTAI_ROW, similarity=28.0),
        match_of(EHENTAI_ROW, similarity=27.99),
        match_of(PANDA_ROW, similarity=10.0),
    ]
    kept = dedupe_matches(matches, min_similarity=28.0)
    assert [m.source for m in kept] == ["nhentai"]


def test_dedupe_filters_everything_below_threshold():
    matches = [match_of(NHENTAI_ROW, similarity=5.0)]
    assert dedupe_matches(matches, min_similarity=28.0) == []


def test_dedupe_handles_empty_and_none():
    assert dedupe_matches([]) == []
    assert dedupe_matches(None) == []


# ---------------------------------------------------------------- plain report


def result_of(rows, *, factor=1.2, **kwargs) -> SoutubotSearchResult:
    return SoutubotSearchResult.from_api(make_payload(rows, factor=factor, **kwargs))


def test_plain_report_with_results():
    result = result_of([NHENTAI_ROW, EHENTAI_ROW, PANDA_ROW], factor=1.2)
    text = format_plain_report(result, base_url="https://soutubot.moe")

    assert "普通模式" in text
    assert "耗时 1.04s" in text
    assert "BugBug 2024-08" in text
    assert "[Digital]" not in text  # 噪声被清洗
    assert "67.45%" in text
    assert "高可信" in text
    assert "https://nhentai.net/g/518943/10" in text
    assert "https://e-hentai.org/s/9f2a1b3c4d/2718281-7" in text
    assert "https://panda.chaika.moe/archive/31415" in text
    assert "第 10 页" in text
    assert "整本" in text  # panda 的 page=0
    assert "日语" in text
    assert "完整结果：https://soutubot.moe/results/2026082615542720" in text
    assert text == text.strip()


def test_plain_report_strict_mode_header():
    result = result_of([NHENTAI_ROW], factor=1.4)
    assert "严格模式" in format_plain_report(result)


def test_plain_report_hides_language_when_disabled():
    result = result_of([NHENTAI_ROW])
    text = format_plain_report(result, show_language=False)
    assert "日语" not in text


def test_plain_report_respects_max_results():
    rows = [
        make_row(NHENTAI_ROW, similarity=70.0),
        make_row(EHENTAI_ROW, similarity=60.0),
        make_row(PANDA_ROW, similarity=50.0),
    ]
    text = format_plain_report(result_of(rows), max_results=1)
    assert "1. " in text
    assert "2. " not in text
    assert "另有 2 条" in text


def test_plain_report_max_results_zero_shows_all():
    rows = [
        make_row(NHENTAI_ROW, similarity=70.0),
        make_row(EHENTAI_ROW, similarity=60.0),
    ]
    text = format_plain_report(result_of(rows), max_results=0)
    assert "1. " in text
    assert "2. " in text
    assert "另有" not in text


def test_plain_report_uses_mirror_override():
    result = result_of([NHENTAI_ROW, EHENTAI_ROW])
    text = format_plain_report(
        result, mirrors={"nhentai": "nhx", "ehentai": "exhentai"}
    )
    assert "https://nhentai.xxx/g/518943/10" in text
    assert "https://exhentai.org/s/9f2a1b3c4d/2718281-7" in text
    assert "nhentai.net" not in text
    assert "ExHentai" in text


def test_plain_report_warns_when_top_similarity_is_medium():
    result = result_of([make_row(NHENTAI_ROW, similarity=30.0)], factor=1.2)
    text = format_plain_report(result)
    assert "仅供参考" in text
    assert "最高相似度偏低" in text


def test_plain_report_no_warning_for_high_confidence():
    result = result_of([make_row(NHENTAI_ROW, similarity=88.0)], factor=1.2)
    assert "最高相似度偏低" not in format_plain_report(result)


def test_plain_report_no_results_branch():
    rows = [make_row(NHENTAI_ROW, similarity=12.34)]
    text = format_plain_report(
        result_of(rows), base_url="https://soutubot.moe"
    )
    assert "没有找到相似度足够高的本子" in text
    assert "12.34%" in text  # 提示最接近的一条
    assert "严格模式" in text or "普通模式" in text
    assert "完整结果：https://soutubot.moe/results/2026082615542720" in text
    assert "https://nhentai.net" not in text  # 低分结果不给链接


def test_plain_report_empty_matches():
    text = format_plain_report(result_of([]))
    assert "没有找到相似度足够高的本子" in text
    assert "最接近的一条" not in text


def test_plain_report_omits_link_when_disabled():
    result = result_of([NHENTAI_ROW])
    text = format_plain_report(
        result, include_result_link=False, base_url="https://soutubot.moe"
    )
    assert "完整结果" not in text


def test_plain_report_no_results_branch_omits_link_when_disabled():
    rows = [make_row(NHENTAI_ROW, similarity=12.34)]
    text = format_plain_report(
        result_of(rows), include_result_link=False, base_url="https://soutubot.moe"
    )
    assert "\u6ca1\u6709\u627e\u5230\u76f8\u4f3c\u5ea6\u8db3\u591f\u9ad8\u7684\u672c\u5b50" in text
    assert "\u5b8c\u6574\u7ed3\u679c" not in text
    assert "https://soutubot.moe/results/" not in text


def test_plain_report_survives_degenerate_rows():
    rows = [
        {"source": "hitomi", "similarity": 99.0},
        {"source": "nhentai", "title": None, "similarity": 50.0},
        {},
    ]
    text = format_plain_report(result_of(rows), base_url="https://soutubot.moe")
    assert "（无标题）" in text
    assert isinstance(text, str) and text


# ---------------------------------------------------------------- llm summary


def test_llm_summary_with_results():
    result = result_of([NHENTAI_ROW, EHENTAI_ROW, PANDA_ROW], factor=1.4)
    text = format_llm_summary(result, base_url="https://soutubot.moe")

    assert "模式=严格" in text
    assert "共 3 条候选" in text
    assert "《BugBug 2024-08》" in text
    assert "相似度 67.45%（高可信）" in text
    assert "来源 nHentai" in text
    assert "语言 日语" in text
    assert "命中第 10 页" in text
    assert "链接 https://nhentai.net/g/518943/10" in text
    assert "完整结果页：https://soutubot.moe/results/2026082615542720" in text
    assert "不要额外编造" in text
    assert "可以作为答案给出" in text


def test_llm_summary_low_confidence_disclaimer():
    result = result_of([make_row(NHENTAI_ROW, similarity=30.0)], factor=1.2)
    text = format_llm_summary(result)
    assert "不要断言" in text
    assert "可以作为答案给出" not in text


def test_llm_summary_omits_result_page_link_without_base_url():
    # main.py \u5173\u6389\u300c\u5b8c\u6574\u7ed3\u679c\u94fe\u63a5\u300d\u65f6\u5c31\u662f\u4f20\u7a7a base_url
    result = result_of([NHENTAI_ROW])
    text = format_llm_summary(result, base_url="")
    assert "\u5b8c\u6574\u7ed3\u679c\u9875" not in text
    assert "https://soutubot.moe/results/" not in text
    assert "\u94fe\u63a5 https://nhentai.net/g/518943/10" in text


def test_llm_summary_without_urls():
    result = result_of([NHENTAI_ROW])
    text = format_llm_summary(result, include_urls=False)
    assert "https://" not in text
    assert "《BugBug 2024-08》" in text


def test_llm_summary_no_results_branch():
    rows = [make_row(NHENTAI_ROW, similarity=9.5)]
    text = format_llm_summary(result_of(rows))
    assert "没有达到可信阈值的结果" in text
    assert "9.50%" in text
    assert "阈值 28%" in text
    assert "不要凭猜测编造书名" in text


def test_llm_summary_no_results_without_any_match():
    text = format_llm_summary(result_of([]))
    assert "最高相似度 无" in text


def test_llm_summary_respects_max_results():
    rows = [
        make_row(NHENTAI_ROW, similarity=70.0),
        make_row(EHENTAI_ROW, similarity=60.0),
        make_row(PANDA_ROW, similarity=50.0),
    ]
    text = format_llm_summary(result_of(rows), max_results=2)
    assert "共 2 条候选" in text
    assert "3. " not in text


def test_llm_summary_uses_mirror_override():
    result = result_of([NHENTAI_ROW])
    text = format_llm_summary(result, mirrors={"nhentai": 1})
    assert "https://nhentai.xxx/g/518943/10" in text


def test_both_renderers_never_raise_on_odd_payloads():
    payloads = [
        make_payload([]),
        make_payload([{}]),
        make_payload([{"source": "nhentai", "similarity": "bad"}]),
        {"data": [{"source": "panda", "subjectPath": "/archive/1", "similarity": 99}]},
        {},
    ]
    for payload in payloads:
        result = SoutubotSearchResult.from_api(payload)
        assert isinstance(format_plain_report(result), str)
        assert isinstance(format_llm_summary(result), str)
