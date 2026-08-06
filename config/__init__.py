# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/config/__init__.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。


import os

from .base_config import *
from .db_config import *


def _env_key(name: str) -> str:
    return f"MEDIA_CRAWLER_{name}"


def _parse_bool(raw: str, default: bool) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def _parse_int(raw: str, default: int) -> int:
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def _apply_env_overrides() -> None:
    bool_overrides = {
        "ENABLE_CDP_MODE",
        "CDP_CONNECT_EXISTING",
        "CDP_HEADLESS",
        "HEADLESS",
        "SAVE_LOGIN_STATE",
        "AUTO_CLOSE_BROWSER",
        "LOGIN_ONLY",
        "ENABLE_IP_PROXY",
        "ENABLE_GET_COMMENTS",
        "ENABLE_GET_SUB_COMMENTS",
        "ENABLE_GET_MEIDAS",
        "XHS_INTERNATIONAL",
    }
    str_overrides = {
        "PLATFORM",
        "LOGIN_TYPE",
        "CRAWLER_TYPE",
        "KEYWORDS",
        "COOKIES",
        "SAVE_DATA_OPTION",
        "SAVE_DATA_PATH",
        "USER_DATA_DIR",
        "STATIC_PROXY_URL",
        "CUSTOM_BROWSER_PATH",
        "IP_PROXY_PROVIDER_NAME",
    }
    int_overrides = {
        "CDP_DEBUG_PORT",
        "BROWSER_LAUNCH_TIMEOUT",
        "START_PAGE",
        "CRAWLER_MAX_NOTES_COUNT",
        "MAX_CONCURRENCY_NUM",
        "IP_PROXY_POOL_COUNT",
        "CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES",
    }

    for name in bool_overrides:
        raw_value = os.getenv(_env_key(name))
        if raw_value is not None:
            globals()[name] = _parse_bool(raw_value, bool(globals().get(name)))

    for name in str_overrides:
        raw_value = os.getenv(_env_key(name))
        if raw_value is not None:
            globals()[name] = raw_value

    for name in int_overrides:
        raw_value = os.getenv(_env_key(name))
        if raw_value is not None:
            globals()[name] = _parse_int(raw_value, int(globals().get(name)))

    proxy_url = os.getenv(_env_key("PROXY_URL"))
    if proxy_url is not None:
        globals()["STATIC_PROXY_URL"] = proxy_url

    ip_proxy_provider = os.getenv(_env_key("IP_PROXY_PROVIDER"))
    if ip_proxy_provider is not None:
        globals()["IP_PROXY_PROVIDER_NAME"] = ip_proxy_provider


_apply_env_overrides()
