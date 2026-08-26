"""来源站点、镜像域名与语言标签映射。

映射关系来自 soutubot.moe 前端（`sourceConfig`）：
- nhentai -> nhentai.net / nhentai.xxx
- ehentai -> e-hentai.org / exhentai.org
- panda   -> panda.chaika.moe
"""

from __future__ import annotations

# 每个来源的展示名
SOURCE_LABELS: dict[str, str] = {
    "nhentai": "nHentai",
    "ehentai": "E-Hentai / ExHentai",
    "panda": "Panda Backup",
}

# 每个来源可选的镜像：(镜像名, 域名)
SOURCE_MIRRORS: dict[str, tuple[tuple[str, str], ...]] = {
    "nhentai": (("NH", "nhentai.net"), ("NHX", "nhentai.xxx")),
    "ehentai": (("E-Hentai", "e-hentai.org"), ("ExHentai", "exhentai.org")),
    "panda": (("Panda", "panda.chaika.moe"),),
}

# soutubot 返回的 language 是 ISO 3166-1 区域码（前端用来渲染国旗）
LANGUAGE_LABELS: dict[str, str] = {
    "jp": "日语",
    "cn": "简体中文",
    "tw": "繁体中文",
    "hk": "繁体中文",
    "gb": "英语",
    "us": "英语",
    "kr": "韩语",
    "ru": "俄语",
    "de": "德语",
    "fr": "法语",
    "es": "西班牙语",
    "pt": "葡萄牙语",
    "it": "意大利语",
    "pl": "波兰语",
    "th": "泰语",
    "vn": "越南语",
    "id": "印尼语",
    "tr": "土耳其语",
    "ua": "乌克兰语",
    "cz": "捷克语",
    "nl": "荷兰语",
    "ar": "阿拉伯语",
    "": "未知",
}


def source_label(source: str) -> str:
    """返回来源的中文/展示名，未知来源原样返回。"""
    return SOURCE_LABELS.get(source, source or "未知来源")


def language_label(language: str) -> str:
    """把 soutubot 的区域码翻译为语言名。"""
    key = (language or "").strip().lower()
    return LANGUAGE_LABELS.get(key, key.upper() or "未知")


def normalize_mirror_choice(source: str, choice: object) -> int:
    """把用户配置的镜像选择（下标 / 镜像名 / 域名）归一化为下标。"""
    mirrors = SOURCE_MIRRORS.get(source)
    if not mirrors:
        return 0

    if isinstance(choice, bool):
        return 0
    if isinstance(choice, int):
        return choice if 0 <= choice < len(mirrors) else 0

    text = str(choice or "").strip().lower()
    if not text:
        return 0
    if text.isdigit():
        index = int(text)
        return index if 0 <= index < len(mirrors) else 0
    for index, (name, host) in enumerate(mirrors):
        if text in {name.lower(), host.lower()}:
            return index
    return 0


def mirror_host(source: str, choice: object = 0) -> str:
    """返回该来源当前选定镜像的域名；未知来源返回空串。"""
    mirrors = SOURCE_MIRRORS.get(source)
    if not mirrors:
        return ""
    return mirrors[normalize_mirror_choice(source, choice)][1]


def mirror_name(source: str, choice: object = 0) -> str:
    """返回该来源当前选定镜像的展示名。"""
    mirrors = SOURCE_MIRRORS.get(source)
    if not mirrors:
        return ""
    return mirrors[normalize_mirror_choice(source, choice)][0]


def describe_mirrors() -> str:
    """生成可直接发给用户的镜像清单文本。"""
    lines: list[str] = []
    for source, mirrors in SOURCE_MIRRORS.items():
        options = " / ".join(f"{name}（{host}）" for name, host in mirrors)
        lines.append(f"· {source_label(source)}[{source}]：{options}")
    return "\n".join(lines)
