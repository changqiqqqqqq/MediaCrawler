# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/xhs/login.py
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


import asyncio
import functools
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

from playwright.async_api import BrowserContext, Page
from tenacity import (RetryError, retry, retry_if_result, stop_after_attempt,
                      wait_fixed)

import config
from base.base_crawler import AbstractLogin
from cache.cache_factory import CacheFactory
from tools import utils


LOGIN_CHECK_MAX_ATTEMPTS = 120
LOGIN_CHECK_INTERVAL_SECONDS = 1


class XiaoHongShuLogin(AbstractLogin):

    def __init__(self,
                 login_type: str,
                 browser_context: BrowserContext,
                 context_page: Page,
                 login_phone: Optional[str] = "",
                 cookie_str: str = "",
                 login_client: Optional[Any] = None,
                 ):
        config.LOGIN_TYPE = login_type
        self.browser_context = browser_context
        self.context_page = context_page
        self.login_phone = login_phone
        self.cookie_str = cookie_str
        # The crawler client can validate the session through the platform API
        # after the QR confirmation, even when the browser page does not redirect.
        self.login_client = login_client
        self._last_api_check = 0.0
        self._last_page_refresh = 0.0
        self._login_started_at = time.monotonic()
        self._verification_announced = False
        self._verification_code_applied = ""
        self._diagnostic_events: list[dict[str, Any]] = []
        self._diagnostic_hooks_attached = False

    def _diagnostic_root(self) -> Path:
        configured = str(os.getenv("MEDIA_CRAWLER_DIAGNOSTIC_OUTPUT") or "").strip()
        if configured:
            return Path(configured)
        qrcode_output = str(os.getenv("MEDIA_CRAWLER_QRCODE_OUTPUT") or "").strip()
        return Path(qrcode_output).parent / "browser_diagnostics" if qrcode_output else Path.cwd() / "browser_diagnostics"

    @staticmethod
    def _diagnostic_url(value: Any) -> str:
        raw = str(value or "")
        try:
            parsed = urlparse(raw)
            if parsed.scheme and parsed.netloc:
                return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))[:500]
        except Exception:
            pass
        return raw[:500]

    def _attach_diagnostic_hooks(self) -> None:
        if self._diagnostic_hooks_attached:
            return
        self._diagnostic_hooks_attached = True

        def record_response(response: Any) -> None:
            try:
                url = str(response.url or "")
                lowered = url.lower()
                if not any(marker in lowered for marker in ("xiaohongshu", "xhs", "login", "selfinfo", "passport", "qr")):
                    return
                self._diagnostic_events.append({
                    "kind": "response",
                    "status": int(response.status),
                    "method": str(response.request.method or ""),
                    "url": self._diagnostic_url(url),
                })
                del self._diagnostic_events[:-120]
            except Exception:
                pass

        def record_request_failed(request: Any) -> None:
            try:
                url = str(request.url or "")
                self._diagnostic_events.append({
                    "kind": "request_failed",
                    "method": str(request.method or ""),
                    "url": self._diagnostic_url(url),
                    "error": str(request.failure or "")[:300],
                })
                del self._diagnostic_events[:-120]
            except Exception:
                pass

        try:
            self.context_page.on("response", record_response)
            self.context_page.on("requestfailed", record_request_failed)
        except Exception:
            pass

    async def _save_login_diagnostics(self, label: str, reason: str = "") -> None:
        """Save browser state for server-side login failures without cookie values."""
        root = self._diagnostic_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            utils.logger.warning(f"[XiaoHongShuLogin] create diagnostics directory failed: {exc}")
            return

        pages = self._iter_context_pages()
        report: dict[str, Any] = {
            "label": label,
            "reason": str(reason or ""),
            "saved_at": time.time(),
            "events": self._diagnostic_events[-120:],
            "pages": [],
        }
        try:
            cookies = await self.browser_context.cookies()
            report["cookies"] = [
                {"name": str(cookie.get("name") or ""), "domain": str(cookie.get("domain") or ""), "path": str(cookie.get("path") or "")}
                for cookie in cookies
            ]
        except Exception as exc:
            report["cookies_error"] = str(exc)[:300]

        for index, page in enumerate(pages):
            page_report: dict[str, Any] = {"index": index, "url": self._diagnostic_url(getattr(page, "url", ""))}
            try:
                page_report["title"] = str(await page.title() or "")
            except Exception:
                page_report["title"] = ""
            try:
                page_report["frames"] = [self._diagnostic_url(frame.url) for frame in page.frames]
            except Exception:
                page_report["frames"] = []
            frame_details: list[dict[str, str]] = []
            try:
                for frame in page.frames:
                    try:
                        frame_text = await frame.locator("body").inner_text(timeout=1200)
                    except Exception:
                        frame_text = ""
                    frame_details.append({
                        "url": self._diagnostic_url(frame.url),
                        "body_text": str(frame_text or "")[-3000:],
                    })
            except Exception:
                pass
            page_report["frame_details"] = frame_details
            try:
                body_text = await page.locator("body").inner_text(timeout=2000)
                page_report["body_text"] = str(body_text or "")[-6000:]
            except Exception as exc:
                page_report["body_error"] = str(exc)[:300]
            try:
                screenshot_path = root / f"{label}-page-{index}.png"
                await page.screenshot(path=str(screenshot_path), full_page=True)
                page_report["screenshot"] = str(screenshot_path)
            except Exception as exc:
                page_report["screenshot_error"] = str(exc)[:300]
            try:
                html_path = root / f"{label}-page-{index}.html"
                html = await page.content()
                html_path.write_text(str(html or "")[:2_000_000], encoding="utf-8")
                page_report["html"] = str(html_path)
            except Exception as exc:
                page_report["html_error"] = str(exc)[:300]
            report["pages"].append(page_report)

        report_path = root / f"{label}.json"
        try:
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            utils.logger.info(f"[XiaoHongShuLogin] login diagnostics saved: {report_path}")
        except Exception as exc:
            utils.logger.warning(f"[XiaoHongShuLogin] write diagnostics failed: {exc}")

    def _iter_context_pages(self) -> list[Page]:
        pages = [self.context_page]
        try:
            pages.extend(self.browser_context.pages)
        except Exception:
            pass

        unique_pages: list[Page] = []
        seen_ids: set[int] = set()
        for page in pages:
            page_id = id(page)
            if page_id in seen_ids:
                continue
            seen_ids.add(page_id)
            unique_pages.append(page)
        return unique_pages

    async def _has_logged_in_page(self) -> bool:
        for page in self._iter_context_pages():
            try:
                current_url = page.url or ""
                if "/user/profile" in current_url:
                    utils.logger.info(
                        f"[XiaoHongShuLogin.check_login_state] Login status confirmed by page URL: {current_url}"
                    )
                    return True
            except Exception:
                continue
        return False

    async def _try_open_login_dialog(self) -> bool:
        login_button_selectors = [
            "xpath=//*[@id='app']/div[1]/div[2]/div[1]/ul/div[1]/button",
            "button:has-text('登录')",
            "text=登录",
        ]
        for selector in login_button_selectors:
            try:
                await self.context_page.locator(selector).first.click(timeout=3000)
                await asyncio.sleep(1)
                utils.logger.info(
                    f"[XiaoHongShuLogin.login_by_qrcode] Clicked login entry with selector: {selector}"
                )
                return True
            except Exception:
                continue
        return False

    @retry(stop=stop_after_attempt(LOGIN_CHECK_MAX_ATTEMPTS), wait=wait_fixed(LOGIN_CHECK_INTERVAL_SECONDS), retry=retry_if_result(lambda value: value is False))
    async def check_login_state(self, no_logged_in_session: str) -> bool:
        """
        Verify login status using dual-check: UI elements and Cookies.
        """
        if await self._has_logged_in_page():
            return True

        # 1. Priority check: Check if the "Me" (Profile) node appears in the sidebar
        try:
            # The profile link exists in the logged-out shell as well. Require
            # the visible profile label so the QR wait is not short-circuited
            # before the user scans the code.
            user_profile_selector = (
                "xpath=//a[contains(@href, '/user/profile/')]["
                ".//span[normalize-space()='我' or normalize-space()='我的' "
                "or normalize-space()='Me'] or "
                "contains(@aria-label, '我') or contains(@aria-label, 'Me')"
                "]"
            )
            
            # Set a short timeout since this is called within a retry loop
            is_visible = await self.context_page.is_visible(user_profile_selector, timeout=500)
            if is_visible:
                utils.logger.info("[XiaoHongShuLogin.check_login_state] Login status confirmed by UI element ('Me' button).")
                return True
        except Exception:
            pass

        # 2. Handle a post-QR SMS challenge through the host UI.
        await self._handle_sms_verification()

        # 3. Alternative: Check for CAPTCHA prompt
        if "请通过验证" in await self.context_page.content():
            utils.logger.info("[XiaoHongShuLogin.check_login_state] CAPTCHA appeared, please verify manually.")

        # 3. Compatibility fallback: Original Cookie-based change detection
        current_cookie = await self.browser_context.cookies()
        _, cookie_dict = utils.convert_cookies(current_cookie)
        current_web_session = cookie_dict.get("web_session")
        
        # If web_session has changed, consider the login successful
        if current_web_session and current_web_session != no_logged_in_session:
            utils.logger.info("[XiaoHongShuLogin.check_login_state] Login status confirmed by Cookie (web_session changed).")
            return True

        # Some server-side QR sessions keep the same page and cookie name after
        # confirmation. Refresh the API client's cookies and query selfinfo as a
        # fallback so headless deployments can observe the completed login.
        if self.login_client and time.monotonic() - self._last_api_check >= 2:
            self._last_api_check = time.monotonic()
            try:
                await self.login_client.update_cookies(
                    browser_context=self.browser_context,
                    urls=getattr(self.login_client, "cookie_urls", None),
                )
                if await self.login_client.pong():
                    utils.logger.info(
                        "[XiaoHongShuLogin.check_login_state] Login status confirmed by selfinfo API."
                    )
                    return True
            except Exception as exc:
                utils.logger.debug(
                    f"[XiaoHongShuLogin.check_login_state] selfinfo API check pending: {exc}"
                )

        return False

    async def _visible_sms_input(self):
        selectors = (
            "input[placeholder*='验证码']", "input[placeholder*='验证']",
            "input[name*='code']",
        )
        for page in self._iter_context_pages():
            for selector in selectors:
                try:
                    locator = page.locator(selector)
                    for index in range(await locator.count()):
                        candidate = locator.nth(index)
                        if await candidate.is_visible():
                            return candidate
                except Exception:
                    continue
        return None

    async def _submit_sms_code(self, input_locator) -> bool:
        for page in self._iter_context_pages():
            try:
                buttons = page.locator("button, [role='button']")
                for index in range(min(await buttons.count(), 80)):
                    button = buttons.nth(index)
                    if not await button.is_visible():
                        continue
                    if (await button.inner_text()).strip() in {"确认", "确定", "登录", "提交", "验证"}:
                        await button.click(timeout=1500)
                        return True
            except Exception:
                continue
        try:
            await input_locator.press("Enter")
            return True
        except Exception:
            return False

    async def _handle_sms_verification(self) -> bool:
        input_locator = await self._visible_sms_input()
        page_text = ""
        for page in self._iter_context_pages():
            try:
                page_text += "\n" + await page.locator("body").inner_text(timeout=800)
            except Exception:
                continue
        challenge_markers = ("请输入验证码", "短信验证码", "验证码已发送", "验证手机号", "安全验证")
        # The QR dialog keeps a hidden phone form and the tab label
        # "验证码登录" in the DOM. Do not treat those generic words as an
        # active SMS challenge before the user has scanned the QR code.
        challenge = any(marker in page_text for marker in challenge_markers)
        if input_locator is not None and any(
            marker in page_text for marker in ("短信验证码", "验证码已发送", "验证手机号", "请输入验证码", "安全验证")
        ):
            challenge = True
        if not challenge:
            return False
        if not self._verification_announced:
            utils.emit_login_verification("sms", "小红书需要手机短信验证码，请在下方输入验证码")
            utils.logger.info("[XiaoHongShuLogin] SMS verification is required; waiting for code from the workbench")
            self._verification_announced = True
        code = utils.read_login_verification_code()
        if code and code != self._verification_code_applied and input_locator is not None:
            await input_locator.fill(code)
            self._verification_code_applied = code
            await self._submit_sms_code(input_locator)
            utils.clear_login_verification_code()
        return True

    async def begin(self):
        """Start login xiaohongshu"""
        utils.logger.info("[XiaoHongShuLogin.begin] Begin login xiaohongshu ...")
        self._attach_diagnostic_hooks()
        if config.LOGIN_TYPE == "qrcode":
            await self.login_by_qrcode()
        elif config.LOGIN_TYPE == "phone":
            await self.login_by_mobile()
        elif config.LOGIN_TYPE == "cookie":
            await self.login_by_cookies()
        else:
            raise ValueError("[XiaoHongShuLogin.begin]I nvalid Login Type Currently only supported qrcode or phone or cookies ...")

    async def login_by_mobile(self):
        """Login xiaohongshu by mobile"""
        utils.logger.info("[XiaoHongShuLogin.login_by_mobile] Begin login xiaohongshu by mobile ...")
        if await self._has_logged_in_page():
            utils.logger.info("[XiaoHongShuLogin.login_by_mobile] Browser is already logged in, skip mobile login.")
            return

        await asyncio.sleep(1)
        try:
            # After entering Xiaohongshu homepage, the login dialog may not pop up automatically, need to manually click login button
            login_button_ele = await self.context_page.wait_for_selector(
                selector="xpath=//*[@id='app']/div[1]/div[2]/div[1]/ul/div[1]/button",
                timeout=5000
            )
            await login_button_ele.click()
            # The login dialog has two forms: one shows phone number and verification code directly
            # The other requires clicking to switch to phone login
            element = await self.context_page.wait_for_selector(
                selector='xpath=//div[@class="login-container"]//div[@class="other-method"]/div[1]',
                timeout=5000
            )
            await element.click()
        except Exception as e:
            utils.logger.info("[XiaoHongShuLogin.login_by_mobile] have not found mobile button icon and keep going ...")

        await asyncio.sleep(1)
        login_container_ele = await self.context_page.wait_for_selector("div.login-container")
        input_ele = await login_container_ele.query_selector("label.phone > input")
        await input_ele.fill(self.login_phone)
        await asyncio.sleep(0.5)

        send_btn_ele = await login_container_ele.query_selector("label.auth-code > span")
        await send_btn_ele.click()  # Click to send verification code
        sms_code_input_ele = await login_container_ele.query_selector("label.auth-code > input")
        submit_btn_ele = await login_container_ele.query_selector("div.input-container > button")
        cache_client = CacheFactory.create_cache(config.CACHE_TYPE_MEMORY)
        max_get_sms_code_time = 60 * 2  # Maximum time to get verification code is 2 minutes
        no_logged_in_session = ""
        while max_get_sms_code_time > 0:
            utils.logger.info(f"[XiaoHongShuLogin.login_by_mobile] get sms code from redis remaining time {max_get_sms_code_time}s ...")
            await asyncio.sleep(1)
            sms_code_key = f"xhs_{self.login_phone}"
            sms_code_value = cache_client.get(sms_code_key)
            if not sms_code_value:
                max_get_sms_code_time -= 1
                continue

            current_cookie = await self.browser_context.cookies()
            _, cookie_dict = utils.convert_cookies(current_cookie)
            no_logged_in_session = cookie_dict.get("web_session")

            await sms_code_input_ele.fill(value=sms_code_value.decode())  # Enter SMS verification code
            await asyncio.sleep(0.5)
            agree_privacy_ele = self.context_page.locator("xpath=//div[@class='agreements']//*[local-name()='svg']")
            await agree_privacy_ele.click()  # Click to agree to privacy policy
            await asyncio.sleep(0.5)

            await submit_btn_ele.click()  # Click login

            # TODO: Should also check if the verification code is correct, as it may be incorrect
            break

        try:
            await self.check_login_state(no_logged_in_session)
        except RetryError:
            utils.logger.info("[XiaoHongShuLogin.login_by_mobile] Login xiaohongshu failed by mobile login method ...")
            sys.exit()

        wait_redirect_seconds = 5
        utils.logger.info(f"[XiaoHongShuLogin.login_by_mobile] Login successful then wait for {wait_redirect_seconds} seconds redirect ...")
        await asyncio.sleep(wait_redirect_seconds)

    async def _refresh_login_qrcode(self, selector: str, initial_qrcode: str) -> None:
        current_qrcode = initial_qrcode
        while True:
            await asyncio.sleep(2)
            try:
                if await self._has_logged_in_page():
                    return
                qrcode_locator = self.context_page.locator(selector).first
                if await qrcode_locator.count() == 0:
                    continue
                next_qrcode = await utils.find_login_qrcode(
                    self.context_page,
                    selector=selector,
                    timeout=1500,
                )
                if next_qrcode and next_qrcode != current_qrcode:
                    utils.emit_login_qrcode(next_qrcode)
                    current_qrcode = next_qrcode
                    utils.logger.info("[XiaoHongShuLogin.login_by_qrcode] Login QR code refreshed.")
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    async def login_by_qrcode(self):
        """login xiaohongshu website and keep webdriver login state"""
        utils.logger.info("[XiaoHongShuLogin.login_by_qrcode] Begin login xiaohongshu by qrcode ...")
        self._attach_diagnostic_hooks()
        if await self._has_logged_in_page():
            utils.logger.info("[XiaoHongShuLogin.login_by_qrcode] Browser is already logged in, skip qrcode login.")
            return

        current_cookie = await self.browser_context.cookies()
        _, cookie_dict = utils.convert_cookies(current_cookie)
        no_logged_in_session = cookie_dict.get("web_session")

        # login_selector = "div.login-container > div.left > div.qrcode > img"
        # Keep this selector narrow. Generic `img[src*='qr']` and `canvas`
        # selectors also match avatars and note media, which made the host
        # replace a valid login QR with unrelated images every few seconds.
        qrcode_img_selector = (
            "img[class*='qrcode-img'], "
            "img[src*='qrcode'], img[src*='qr_code'], "
            "img[alt*='二维码'], img[aria-label*='二维码'], "
            "[class*='qrcode'] canvas, [class*='qr-code'] canvas, "
            "[class*='qrcode'] svg, [class*='qr-code'] svg"
        )
        # find login qrcode
        base64_qrcode_img = await utils.find_login_qrcode(
            self.context_page,
            selector=qrcode_img_selector,
            timeout=5000,
        )
        if not base64_qrcode_img:
            utils.logger.info("[XiaoHongShuLogin.login_by_qrcode] QR code not found automatically, trying to open login dialog ...")
            await asyncio.sleep(0.5)
            await self._try_open_login_dialog()
            base64_qrcode_img = await utils.find_login_qrcode(
                self.context_page,
                selector=qrcode_img_selector,
                timeout=10000,
            )
            if not base64_qrcode_img:
                utils.logger.info("[XiaoHongShuLogin.login_by_qrcode] QR code still not found; keep browser open for manual login.")
                await self._save_login_diagnostics("qrcode-not-found", "小红书登录页未找到二维码")

        if base64_qrcode_img:
            # Show the QR code in a separate image viewer when it is available.
            partial_show_qrcode = functools.partial(utils.show_qrcode, base64_qrcode_img)
            asyncio.get_running_loop().run_in_executor(executor=None, func=partial_show_qrcode)

        qrcode_refresh_task = asyncio.create_task(self._refresh_login_qrcode(qrcode_img_selector, base64_qrcode_img))
        utils.logger.info(f"[XiaoHongShuLogin.login_by_qrcode] waiting for scan code login, remaining time is 120s")
        try:
            await self.check_login_state(no_logged_in_session)
        except RetryError:
            utils.logger.info("[XiaoHongShuLogin.login_by_qrcode] Login xiaohongshu failed by qrcode login method ...")
            await self._save_login_diagnostics("qrcode-login-failed", "二维码扫码后登录状态确认超时")
            sys.exit()
        finally:
            qrcode_refresh_task.cancel()
            try:
                await qrcode_refresh_task
            except asyncio.CancelledError:
                pass

        wait_redirect_seconds = 5
        utils.logger.info(f"[XiaoHongShuLogin.login_by_qrcode] Login successful then wait for {wait_redirect_seconds} seconds redirect ...")
        await asyncio.sleep(wait_redirect_seconds)

    async def login_by_cookies(self):
        """login xiaohongshu website by cookies"""
        utils.logger.info("[XiaoHongShuLogin.login_by_cookies] Begin login xiaohongshu by cookie ...")
        for key, value in utils.convert_str_cookie_to_dict(self.cookie_str).items():
            if key != "web_session":  # Only set web_session cookie attribute
                continue
            await self.browser_context.add_cookies([{
                'name': key,
                'value': value,
                'domain': ".rednote.com" if config.XHS_INTERNATIONAL else ".xiaohongshu.com",
                'path': "/"
            }])
