"""mirrors.py：来源标签、镜像归一化与域名拼装。"""

from __future__ import annotations

import pytest

from soutubot.mirrors import (
    LANGUAGE_LABELS,
    SOURCE_LABELS,
    SOURCE_MIRRORS,
    describe_mirrors,
    language_label,
    mirror_host,
    mirror_name,
    normalize_mirror_choice,
    source_label,
)


def test_three_known_sources_have_expected_mirrors():
    assert set(SOURCE_MIRRORS) == {"nhentai", "ehentai", "panda"}
    assert SOURCE_MIRRORS["nhentai"] == (("NH", "nhentai.net"), ("NHX", "nhentai.xxx"))
    assert SOURCE_MIRRORS["ehentai"] == (
        ("E-Hentai", "e-hentai.org"),
        ("ExHentai", "exhentai.org"),
    )
    assert SOURCE_MIRRORS["panda"] == (("Panda", "panda.chaika.moe"),)
    # 每个来源都必须有展示名
    assert set(SOURCE_LABELS) == set(SOURCE_MIRRORS)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("nhentai", "nHentai"),
        ("ehentai", "E-Hentai / ExHentai"),
        ("panda", "Panda Backup"),
    ],
)
def test_source_label_known(source, expected):
    assert source_label(source) == expected


def test_source_label_unknown_and_empty():
    assert source_label("hitomi") == "hitomi"
    assert source_label("") == "未知来源"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("jp", "日语"),
        ("JP", "日语"),
        ("  cn  ", "简体中文"),
        ("tw", "繁体中文"),
        ("hk", "繁体中文"),
        ("gb", "英语"),
        ("us", "英语"),
        ("kr", "韩语"),
        ("", "未知"),
    ],
)
def test_language_label_known(code, expected):
    assert language_label(code) == expected


def test_language_label_unknown_falls_back_to_uppercase():
    assert language_label("zz") == "ZZ"
    assert language_label(None) == "未知"
    # 映射表里必须包含空串兜底项
    assert LANGUAGE_LABELS[""] == "未知"


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        (0, 0),
        (1, 1),
        (2, 0),  # 越界回退
        (-1, 0),
        (True, 0),  # bool 不当作 int 下标
        (False, 0),
        ("0", 0),
        ("1", 1),
        ("9", 0),
        ("NH", 0),
        ("nhx", 1),  # 镜像名大小写不敏感
        ("NHX", 1),
        ("nhentai.net", 0),
        ("nhentai.xxx", 1),
        ("  NHENTAI.XXX  ", 1),
        ("exhentai.org", 0),  # 不属于 nhentai 的域名 -> 回退
        ("", 0),
        (None, 0),
        ("garbage", 0),
        (1.9, 0),  # float 既不是 int 也不是数字串
    ],
)
def test_normalize_mirror_choice_nhentai(choice, expected):
    assert normalize_mirror_choice("nhentai", choice) == expected


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ("exhentai", 1),
        ("ExHentai", 1),
        ("exhentai.org", 1),
        ("e-hentai.org", 0),
        ("e-hentai", 0),
        (1, 1),
    ],
)
def test_normalize_mirror_choice_ehentai(choice, expected):
    assert normalize_mirror_choice("ehentai", choice) == expected


def test_normalize_mirror_choice_panda_single_mirror():
    assert normalize_mirror_choice("panda", 0) == 0
    assert normalize_mirror_choice("panda", 1) == 0
    assert normalize_mirror_choice("panda", "panda.chaika.moe") == 0


def test_normalize_mirror_choice_unknown_source():
    assert normalize_mirror_choice("hitomi", 1) == 0
    assert normalize_mirror_choice("", "whatever") == 0


@pytest.mark.parametrize(
    ("source", "choice", "host", "name"),
    [
        ("nhentai", 0, "nhentai.net", "NH"),
        ("nhentai", 1, "nhentai.xxx", "NHX"),
        ("nhentai", "nhx", "nhentai.xxx", "NHX"),
        ("ehentai", 0, "e-hentai.org", "E-Hentai"),
        ("ehentai", "exhentai", "exhentai.org", "ExHentai"),
        ("panda", 0, "panda.chaika.moe", "Panda"),
    ],
)
def test_mirror_host_and_name(source, choice, host, name):
    assert mirror_host(source, choice) == host
    assert mirror_name(source, choice) == name


def test_mirror_host_defaults_to_first_mirror():
    assert mirror_host("nhentai") == "nhentai.net"
    assert mirror_name("ehentai") == "E-Hentai"


def test_mirror_host_unknown_source_returns_empty():
    assert mirror_host("hitomi", 0) == ""
    assert mirror_name("hitomi", 0) == ""


def test_describe_mirrors_lists_every_source_and_host():
    text = describe_mirrors()
    lines = text.splitlines()
    assert len(lines) == len(SOURCE_MIRRORS)
    for source, mirrors in SOURCE_MIRRORS.items():
        assert f"[{source}]" in text
        assert source_label(source) in text
        for name, host in mirrors:
            assert name in text
            assert host in text
