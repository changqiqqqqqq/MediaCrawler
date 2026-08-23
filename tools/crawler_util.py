# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tools/crawler_util.py
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


# -*- coding: utf-8 -*-
# @Author  : relakkes@gmail.com
# @Time    : 2023/12/2 12:53
# @Desc    : Crawler utility functions

import base64
import json
import os
import random
import re
import time
import urllib
import urllib.parse
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple, cast

import httpx
from PIL import Image, ImageDraw, ImageShow
from playwright.async_api import BrowserContext, Cookie, Page

from . import utils
from .httpx_util import make_async_client


async def find_login_qrcode(page: Page, selector: str, timeout: int = 30000) -> str:
    """find login qrcode image from target selector"""
    try:
        elements = await page.wait_for_selector(
            selector=selector,
            timeout=timeout,
        )
        # ElementHandle.get_property() returns a JSHandle.  Converting that
        # handle with str() produces an empty/non-image value on current
        # Playwright releases, so QR login appeared to start but the host never
        # received a usable image.  Read the DOM attribute first and only use
        # the property as a compatibility fallback.
        login_qrcode_img = await elements.get_attribute("src")  # type: ignore
        if not login_qrcode_img:
            source_handle = await elements.get_property("src")  # type: ignore
            login_qrcode_img = await source_handle.json_value() if source_handle else ""
        login_qrcode_img = str(login_qrcode_img or "").strip()
        # Several current login pages render the QR code as a canvas, SVG, or
        # a background image rather than an <img src>.  Capture the matched
        # element so the host still receives a usable PNG in those cases.
        if not login_qrcode_img:
            screenshot = await elements.screenshot()  # type: ignore
            return base64.b64encode(screenshot).decode("utf-8")
        if "http://" in login_qrcode_img or "https://" in login_qrcode_img:
            try:
                async with make_async_client(follow_redirects=True) as client:
                    utils.logger.info(f"[find_login_qrcode] get qrcode by url:{login_qrcode_img}")
                    resp = await client.get(login_qrcode_img, headers={"User-Agent": get_user_agent()})
                    if resp.status_code == 200 and resp.content:
                        image_data = resp.content
                        base64_image = base64.b64encode(image_data).decode('utf-8')
                        return base64_image
                    utils.logger.warning(
                        f"[find_login_qrcode] remote qrcode fetch returned {resp.status_code}; "
                        "falling back to the browser-rendered image"
                    )
            except Exception as exc:
                # Some platforms allow the browser to load the QR image but
                # reject a second server-side HTTP request. The rendered DOM
                # image is still a valid QR code, so capture it directly.
                utils.logger.warning(
                    f"[find_login_qrcode] remote qrcode fetch failed: {exc}; "
                    "falling back to the browser-rendered image"
                )
            screenshot = await elements.screenshot()  # type: ignore
            return base64.b64encode(screenshot).decode("utf-8")
        return login_qrcode_img

    except Exception as e:
        print(e)
        return ""


async def find_qrcode_img_from_canvas(page: Page, canvas_selector: str) -> str:
    """
    find qrcode image from canvas element
    Args:
        page:
        canvas_selector:

    Returns:

    """

    # Wait for Canvas element to load
    canvas = await page.wait_for_selector(canvas_selector)

    # Take screenshot of Canvas element
    screenshot = await canvas.screenshot()

    # Convert screenshot to base64 format
    base64_image = base64.b64encode(screenshot).decode('utf-8')
    return base64_image


def _write_login_qrcode_output(qr_code: str) -> None:
    output_path = os.getenv("MEDIA_CRAWLER_QRCODE_OUTPUT", "").strip()
    if not output_path:
        return
    try:
        normalized_qrcode = qr_code.split(",", 1)[1] if "," in qr_code else qr_code
        payload = {
            "qrcode": normalized_qrcode,
            "data_uri": f"data:image/png;base64,{normalized_qrcode}",
            "updated_at": time.time(),
        }
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target.parent / f".{target.name}.tmp"
        temp_target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp_target.replace(target)
    except Exception as exc:
        utils.logger.warning(f"[show_qrcode] write qrcode output failed: {exc}")


def emit_login_qrcode(qr_code: str) -> None:
    """Publish a QR code to the embedding application without opening a viewer."""
    _write_login_qrcode_output(qr_code)


def emit_login_cookies(cookie_header: str) -> None:
    """Persist validated browser cookies for the embedding application's next job."""
    output_path = os.getenv("MEDIA_CRAWLER_COOKIE_OUTPUT", "").strip()
    cookie_header = str(cookie_header or "").strip()
    if not output_path or not cookie_header:
        return
    try:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"cookies": cookie_header, "updated_at": time.time()}
        temp_target = target.parent / f".{target.name}.tmp"
        temp_target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp_target.replace(target)
    except Exception as exc:
        utils.logger.warning(f"[emit_login_cookies] write session output failed: {exc}")


def emit_login_verification(kind: str = "sms", message: str = "", options: list[dict[str, str]] | None = None) -> None:
    """Publish a human verification step to the embedding application."""
    output_path = os.getenv("MEDIA_CRAWLER_VERIFICATION_OUTPUT", "").strip()
    if not output_path:
        return
    try:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "required": True,
            "kind": str(kind or "sms"),
            "message": str(message or "请输入手机短信验证码"),
            "options": options if isinstance(options, list) else [],
            "updated_at": time.time(),
        }
        temp_target = target.parent / f".{target.name}.tmp"
        temp_target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp_target.replace(target)
    except Exception as exc:
        utils.logger.warning(f"[emit_login_verification] write verification output failed: {exc}")


def read_login_verification_code() -> str:
    """Read a code submitted by the embedding application."""
    input_path = os.getenv("MEDIA_CRAWLER_VERIFICATION_INPUT", "").strip()
    if not input_path:
        return ""
    try:
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
        code = str(payload.get("code") or "").strip()
        return code if re.fullmatch(r"[0-9]{4,8}", code) else ""
    except Exception:
        return ""


def read_login_verification_request() -> dict:
    """Read an action or code submitted by the embedding application."""
    input_path = os.getenv("MEDIA_CRAWLER_VERIFICATION_INPUT", "").strip()
    if not input_path:
        return {}
    try:
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def clear_login_verification_code() -> None:
    input_path = os.getenv("MEDIA_CRAWLER_VERIFICATION_INPUT", "").strip()
    if not input_path:
        return
    try:
        Path(input_path).unlink(missing_ok=True)
    except Exception:
        pass


def show_qrcode(qr_code) -> None:  # type: ignore
    """parse base64 encode qrcode image and show it"""
    _write_login_qrcode_output(qr_code)
    disable_viewer = os.getenv("MEDIA_CRAWLER_DISABLE_QR_VIEWER", "").strip().lower() in {"1", "true", "t", "yes", "y", "on"}
    if disable_viewer:
        return
    if "," in qr_code:
        qr_code = qr_code.split(",")[1]
    qr_code = base64.b64decode(qr_code)
    image = Image.open(BytesIO(qr_code))

    # Add a square border around the QR code and display it within the border to improve scanning accuracy.
    width, height = image.size
    new_image = Image.new('RGB', (width + 20, height + 20), color=(255, 255, 255))
    new_image.paste(image, (10, 10))
    draw = ImageDraw.Draw(new_image)
    draw.rectangle((0, 0, width + 19, height + 19), outline=(0, 0, 0), width=1)
    del ImageShow.UnixViewer.options["save_all"]
    new_image.show()


def get_user_agent() -> str:
    ua_list = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.5112.79 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.5060.53 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.84 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.5112.79 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.5060.53 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.4844.84 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5112.79 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5060.53 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.4844.84 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.5112.79 Safari/537.36"
    ]
    return random.choice(ua_list)


def get_mobile_user_agent() -> str:
    ua_list = [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
    ]
    return random.choice(ua_list)


def convert_cookies(cookies: Optional[List[Cookie]]) -> Tuple[str, Dict]:
    if not cookies:
        return "", {}
    cookies_str = ";".join([f"{cookie.get('name')}={cookie.get('value')}" for cookie in cookies])
    cookie_dict = dict()
    for cookie in cookies:
        cookie_dict[cookie.get('name')] = cookie.get('value')
    return cookies_str, cookie_dict


async def convert_browser_context_cookies(
    browser_context: BrowserContext, urls: Optional[List[str]] = None
) -> Tuple[str, Dict]:
    cookies = (
        await browser_context.cookies(urls=urls)
        if urls
        else await browser_context.cookies()
    )
    return convert_cookies(cookies)


def convert_str_cookie_to_dict(cookie_str: str) -> Dict:
    cookie_dict: Dict[str, str] = dict()
    if not cookie_str:
        return cookie_dict
    for cookie in cookie_str.split(";"):
        cookie = cookie.strip()
        if not cookie:
            continue
        cookie_list = cookie.split("=")
        if len(cookie_list) != 2:
            continue
        cookie_value = cookie_list[1]
        if isinstance(cookie_value, list):
            cookie_value = "".join(cookie_value)
        cookie_dict[cookie_list[0]] = cookie_value
    return cookie_dict


def match_interact_info_count(count_str: str) -> int:
    if not count_str:
        return 0

    match = re.search(r'\d+', count_str)
    if match:
        number = match.group()
        return int(number)
    else:
        return 0


def format_proxy_info(ip_proxy_info) -> Tuple[Optional[Dict], Optional[str]]:
    """format proxy info for playwright and httpx"""
    # fix circular import issue
    from proxy.proxy_ip_pool import IpInfoModel
    ip_proxy_info = cast(IpInfoModel, ip_proxy_info)

    # Playwright proxy server should be in format "host:port" without protocol prefix
    server = f"{ip_proxy_info.ip}:{ip_proxy_info.port}"
    
    playwright_proxy = {
        "server": server,
    }
    
    # Only add username and password if they are not empty
    if ip_proxy_info.user and ip_proxy_info.password:
        playwright_proxy["username"] = ip_proxy_info.user
        playwright_proxy["password"] = ip_proxy_info.password
    
    # httpx 0.28.1 requires passing proxy URL string directly, not a dictionary
    if ip_proxy_info.user and ip_proxy_info.password:
        httpx_proxy = f"http://{ip_proxy_info.user}:{ip_proxy_info.password}@{ip_proxy_info.ip}:{ip_proxy_info.port}"
    else:
        httpx_proxy = f"http://{ip_proxy_info.ip}:{ip_proxy_info.port}"
    return playwright_proxy, httpx_proxy


def extract_text_from_html(html: str) -> str:
    """Extract text from HTML, removing all tags."""
    if not html:
        return ""

    # Remove script and style elements
    clean_html = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL)
    # Remove all other tags
    clean_text = re.sub(r'<[^>]+>', '', clean_html).strip()
    return clean_text

def extract_url_params_to_dict(url: str) -> Dict:
    """Extract URL parameters to dict"""
    url_params_dict = dict()
    if not url:
        return url_params_dict
    parsed_url = urllib.parse.urlparse(url)
    url_params_dict = dict(urllib.parse.parse_qsl(parsed_url.query))
    return url_params_dict
