"""models.py：三种来源的解析、URL 拼装、去重键与缓存往返。"""

from __future__ import annotations

import pytest
from conftest import EHENTAI_ROW, NHENTAI_ROW, PANDA_ROW, make_payload, make_row

from soutubot.models import SoutubotMatch, SoutubotSearchResult


# ---------------------------------------------------------------- from_api


def test_from_api_parses_nhentai_row(nhentai_row):
    match = SoutubotMatch.from_api(nhentai_row)
    assert match.source == "nhentai"
    assert match.page == 10
    assert match.title == "BugBug 2024-08 [Digital]"
    assert match.language == "jp"
    assert match.page_path == "/g/518943/10"
    assert match.subject_path == "/g/518943"
    assert match.preview_image_url.endswith("0010.webp")
    assert match.similarity == pytest.approx(67.45)
    assert match.raw == nhentai_row
    assert match.raw is not nhentai_row  # 必须是拷贝


def test_from_api_normalizes_case_and_whitespace():
    match = SoutubotMatch.from_api(
        make_row(NHENTAI_ROW, source="NHentai", language="JP", title="  标题  ")
    )
    assert match.source == "nhentai"
    assert match.language == "jp"
    assert match.title == "标题"


def test_from_api_panda_null_page_path(panda_row):
    match = SoutubotMatch.from_api(panda_row)
    assert match.source == "panda"
    assert match.page == 0
    assert match.page_path == ""  # null -> 空串
    assert match.subject_path == "/archive/31415"


def test_from_api_tolerates_bad_values():
    match = SoutubotMatch.from_api(
        {"source": None, "page": "abc", "similarity": "not-a-number"}
    )
    assert match.source == ""
    assert match.page == 0
    assert match.similarity == 0.0
    assert match.title == ""


def test_from_api_accepts_numeric_strings():
    match = SoutubotMatch.from_api({"page": "12", "similarity": "45.5"})
    assert match.page == 12
    assert match.similarity == pytest.approx(45.5)


def test_from_api_empty_payload():
    match = SoutubotMatch.from_api({})
    assert match.source == ""
    assert match.raw == {}


# ---------------------------------------------------------------- 展示属性


def test_source_and_language_names(nhentai_row, ehentai_row, panda_row):
    assert SoutubotMatch.from_api(nhentai_row).source_name == "nHentai"
    assert SoutubotMatch.from_api(ehentai_row).source_name == "E-Hentai / ExHentai"
    assert SoutubotMatch.from_api(panda_row).source_name == "Panda Backup"
    assert SoutubotMatch.from_api(nhentai_row).language_name == "日语"
    assert SoutubotMatch.from_api(ehentai_row).language_name == "简体中文"
    assert SoutubotMatch.from_api(panda_row).language_name == "繁体中文"


@pytest.mark.parametrize(
    ("row", "gallery_id"),
    [
        (NHENTAI_ROW, "518943"),
        (EHENTAI_ROW, "2718281"),
        (PANDA_ROW, "31415"),
    ],
)
def test_gallery_id_for_each_source(row, gallery_id):
    assert SoutubotMatch.from_api(row).gallery_id == gallery_id


def test_gallery_id_falls_back_to_page_path():
    match = SoutubotMatch.from_api(
        make_row(NHENTAI_ROW, subjectPath=None, pagePath="/g/777/3")
    )
    assert match.subject_path == ""
    assert match.gallery_id == "777"


def test_gallery_id_empty_when_no_digits():
    match = SoutubotMatch.from_api(
        make_row(NHENTAI_ROW, subjectPath="/unknown/path", pagePath=None)
    )
    assert match.gallery_id == ""


def test_dedupe_key_uses_source_and_subject_path(nhentai_row):
    match = SoutubotMatch.from_api(nhentai_row)
    assert match.dedupe_key == ("nhentai", "/g/518943")

    other_page = SoutubotMatch.from_api(make_row(NHENTAI_ROW, page=11, pagePath="/g/518943/11"))
    # 同一本书的不同页共享去重键
    assert other_page.dedupe_key == match.dedupe_key


def test_dedupe_key_falls_back_to_page_path():
    match = SoutubotMatch.from_api(make_row(NHENTAI_ROW, subjectPath=None))
    assert match.dedupe_key == ("nhentai", "/g/518943/10")


# ---------------------------------------------------------------- URL 拼装


def test_nhentai_urls_default_and_alternate_mirror(nhentai_row):
    match = SoutubotMatch.from_api(nhentai_row)
    assert match.subject_url() == "https://nhentai.net/g/518943"
    assert match.page_url() == "https://nhentai.net/g/518943/10"
    assert match.best_url() == "https://nhentai.net/g/518943/10"

    assert match.subject_url(1) == "https://nhentai.xxx/g/518943"
    assert match.page_url("nhx") == "https://nhentai.xxx/g/518943/10"
    assert match.best_url("nhentai.xxx") == "https://nhentai.xxx/g/518943/10"


def test_ehentai_urls_default_and_exhentai_mirror(ehentai_row):
    match = SoutubotMatch.from_api(ehentai_row)
    assert match.subject_url() == "https://e-hentai.org/g/2718281/1a2b3c4d5e"
    assert match.page_url() == "https://e-hentai.org/s/9f2a1b3c4d/2718281-7"
    assert match.subject_url("exhentai") == "https://exhentai.org/g/2718281/1a2b3c4d5e"
    assert match.page_url(1) == "https://exhentai.org/s/9f2a1b3c4d/2718281-7"


def test_panda_page_url_falls_back_to_subject_url(panda_row):
    match = SoutubotMatch.from_api(panda_row)
    assert match.subject_url() == "https://panda.chaika.moe/archive/31415"
    # pagePath 为 null -> page_url 必须回退到画廊地址
    assert match.page_url() == match.subject_url()
    assert match.best_url() == "https://panda.chaika.moe/archive/31415"
    # panda 只有一个镜像，任何选择都指向同一域名
    assert match.best_url(5) == "https://panda.chaika.moe/archive/31415"


def test_urls_empty_for_unknown_source():
    match = SoutubotMatch.from_api(make_row(NHENTAI_ROW, source="hitomi"))
    assert match.subject_url() == ""
    assert match.page_url() == ""
    assert match.best_url() == ""


def test_urls_empty_when_both_paths_missing():
    match = SoutubotMatch.from_api(
        make_row(NHENTAI_ROW, subjectPath=None, pagePath=None)
    )
    assert match.subject_url() == ""
    assert match.page_url() == ""
    assert match.best_url() == ""


# ---------------------------------------------------------------- SearchResult


def test_search_result_from_api(full_payload):
    result = SoutubotSearchResult.from_api(full_payload)
    assert result.result_id == "2026082615542720"
    assert result.factor == pytest.approx(1.4)
    assert result.image_url == "https://img.soutubot.moe/upload/x.webp"
    assert result.search_option == "api 1.4 Liner 64"
    assert result.execution_time == pytest.approx(1.04)
    assert [m.source for m in result.matches] == ["nhentai", "ehentai", "panda"]


def test_search_result_skips_non_dict_rows():
    payload = make_payload([NHENTAI_ROW])
    payload["data"].extend(["oops", None, 42])
    result = SoutubotSearchResult.from_api(payload)
    assert len(result.matches) == 1


def test_search_result_handles_missing_data_key():
    result = SoutubotSearchResult.from_api({"id": "1", "data": None})
    assert result.matches == []
    assert result.best_similarity == 0.0


def test_search_result_empty_payload():
    result = SoutubotSearchResult.from_api({})
    assert result.result_id == ""
    assert result.factor == 0.0
    assert result.matches == []


@pytest.mark.parametrize(
    ("factor", "strict"),
    [(1.2, False), (1.19, False), (1.21, True), (1.4, True), (0.0, False)],
)
def test_strict_threshold(factor, strict):
    result = SoutubotSearchResult.from_api(make_payload([NHENTAI_ROW], factor=factor))
    assert result.strict is strict


def test_best_similarity(full_payload):
    result = SoutubotSearchResult.from_api(full_payload)
    assert result.best_similarity == pytest.approx(67.45)


def test_result_page_url(full_payload):
    result = SoutubotSearchResult.from_api(full_payload)
    assert (
        result.result_page_url("https://soutubot.moe")
        == "https://soutubot.moe/results/2026082615542720"
    )
    # 末尾斜杠要被吃掉
    assert (
        result.result_page_url("https://soutubot.moe/")
        == "https://soutubot.moe/results/2026082615542720"
    )


def test_result_page_url_empty_without_id():
    result = SoutubotSearchResult.from_api(make_payload([], result_id=""))
    assert result.result_page_url("https://soutubot.moe") == ""


def test_to_cache_keeps_api_field_names(full_payload):
    result = SoutubotSearchResult.from_api(full_payload)
    cached = result.to_cache()
    assert set(cached) == {
        "id",
        "factor",
        "imageUrl",
        "searchOption",
        "executionTime",
        "data",
    }
    assert cached["id"] == "2026082615542720"
    assert cached["data"][0]["pagePath"] == "/g/518943/10"
    assert cached["data"][2]["pagePath"] is None  # panda 的 null 原样保留


def test_to_cache_round_trip(full_payload):
    result = SoutubotSearchResult.from_api(full_payload)
    restored = SoutubotSearchResult.from_api(result.to_cache())
    assert restored == result
    assert [m.best_url() for m in restored.matches] == [
        m.best_url() for m in result.matches
    ]


def test_to_cache_is_json_serializable(full_payload):
    import json

    result = SoutubotSearchResult.from_api(full_payload)
    text = json.dumps(result.to_cache(), ensure_ascii=False)
    again = SoutubotSearchResult.from_api(json.loads(text))
    assert again == result
