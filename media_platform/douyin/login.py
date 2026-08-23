# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/douyin/login.py
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
import sys
import time
from typing import Optional

from playwright.async_api import BrowserContext, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from tenacity import (RetryError, retry, retry_if_result, stop_after_attempt,
                      wait_fixed)

import config
from base.base_crawler import AbstractLogin
from cache.cache_factory import CacheFactory
from tools import utils


class DouYinLogin(AbstractLogin):

    def __init__(self,
                 login_type: str,
                 browser_context: BrowserContext, # type: ignore
                 context_page: Page, # type: ignore
                 login_phone: Optional[str] = "",
                 cookie_str: Optional[str] = ""
                 ):
        config.LOGIN_TYPE = login_type
        self.browser_context = browser_context
        self.context_page = context_page
        self.login_phone = login_phone
        self.scan_qrcode_time = 60
        self.cookie_str = cookie_str

    async def begin(self):
        """
            Start login douyin website
            The verification accuracy of the slider verification is not very good... If there are no special requirements, it is recommended not to use Douyin login, or use cookie login
        """

        # Cookie login does not need the interactive dialog.  On current
        # Douyin pages the dialog may not exist at all, so waiting for it
        # makes an otherwise valid cookie login fail before injection.
        if config.LOGIN_TYPE != "cookie":
            await self.popup_login_dialog()

        # select login type
        if config.LOGIN_TYPE == "qrcode":
            await self.login_by_qrcode()
        elif config.LOGIN_TYPE == "phone":
            await self.login_by_mobile()
        elif config.LOGIN_TYPE == "cookie":
            await self.login_by_cookies()
        else:
            raise ValueError("[DouYinLogin.begin] Invalid Login Type Currently only supported qrcode or phone or cookie ...")

        # If the page redirects to the slider verification page, need to slide again
        await asyncio.sleep(6)
        current_page_title = await self.context_page.title()
        if "验证码中间页" in current_page_title:
            await self.check_page_display_slider(move_step=3, slider_level="hard")

        # check login state
        utils.logger.info(f"[DouYinLogin.begin] login finished then check login state ...")
        try:
            await self.check_login_state()
        except RetryError:
            utils.logger.info("[DouYinLogin.begin] login failed please confirm ...")
            sys.exit()

        # wait for redirect
        wait_redirect_seconds = 5
        utils.logger.info(f"[DouYinLogin.begin] Login successful then wait for {wait_redirect_seconds} seconds redirect ...")
        await asyncio.sleep(wait_redirect_seconds)

    @retry(stop=stop_after_attempt(600), wait=wait_fixed(1), retry=retry_if_result(lambda value: value is False))
    async def check_login_state(self):
        """Check if the current login status is successful and return True otherwise return False"""
        current_cookie = await self.browser_context.cookies()
        _, cookie_dict = utils.convert_cookies(current_cookie)
        authenticated_cookie_names = {
            "sessionid", "sessionid_ss", "sid_guard", "sid_tt",
            "uid_tt", "uid_tt_ss", "login_status",
        }
        if any(
            str(value or "").strip()
            for name, value in cookie_dict.items()
            if str(name or "").lower() in authenticated_cookie_names
        ):
            return True
        return False

    async def popup_login_dialog(self):
        """If the login dialog box does not pop up automatically, we will manually click the login button"""
        dialog_selectors = (
            "#login-panel-new, [id*='login-panel'], [class*='login-panel'], "
            "[class*='login-dialog'], [class*='qrcode'], "
            "img[aria-label*='二维码'], img[src*='qrcode']"
        )
        dialog_selector = dialog_selectors
        dialog = self.context_page.locator(dialog_selector).first
        try:
            if await dialog.count() and await dialog.is_visible():
                return
        except Exception:
            pass
        try:
            # check dialog box is auto popup and wait for 10 seconds
            await self.context_page.wait_for_selector(dialog_selector, timeout=1000 * 10)
        except Exception as e:
            utils.logger.error(f"[DouYinLogin.popup_login_dialog] login dialog box does not pop up automatically, error: {e}")
            try:
                if await dialog.count() and await dialog.is_visible():
                    return
            except Exception:
                pass
            utils.logger.info("[DouYinLogin.popup_login_dialog] login dialog box does not pop up automatically, we will manually click the login button")
            # Douyin has changed the trigger from a <p> to a button several
            # times.  Use the visible text as the stable contract and retain
            # the old selector as a fallback for older page variants.
            for selector in (
                "button:has-text('登录')",
                "[role='button']:has-text('登录')",
                "p:has-text('登录')",
                "xpath=//p[text() = '登录']",
            ):
                try:
                    login_button_ele = self.context_page.locator(selector).first
                    if await login_button_ele.is_visible():
                        await login_button_ele.click(timeout=15000, force=True)
                        break
                except Exception:
                    continue
            # The new Douyin dialog no longer has #login-panel-new. Wait for
            # any login/QR container instead of requiring the legacy id.
            await self.context_page.wait_for_selector(dialog_selector, timeout=15000)

    async def login_by_qrcode(self):
        utils.logger.info("[DouYinLogin.login_by_qrcode] Begin login douyin by qrcode...")
        qrcode_img_selector = (
            "img[aria-label='二维码'], "
            "#animate_qrcode_container img, "
            "#login-panel-new img[src^='data:image'], "
            "[class*='login-panel'] img, [class*='login-dialog'] img, "
            "img[src*='qrcode'], img[aria-label*='二维码']"
        )
        base64_qrcode_img = await utils.find_login_qrcode(
            self.context_page,
            selector=qrcode_img_selector
        )
        if not base64_qrcode_img:
            utils.logger.info("[DouYinLogin.login_by_qrcode] login qrcode not found please confirm ...")
            sys.exit()

        partial_show_qrcode = functools.partial(utils.show_qrcode, base64_qrcode_img)
        asyncio.get_running_loop().run_in_executor(executor=None, func=partial_show_qrcode)
        await asyncio.sleep(2)
        await self.wait_for_qrcode_login()

    async def _has_login_state(self) -> bool:
        current_cookie = await self.browser_context.cookies()
        _, cookie_dict = utils.convert_cookies(current_cookie)
        authenticated_cookie_names = {
            "sessionid", "sessionid_ss", "sid_guard", "sid_tt",
            "uid_tt", "uid_tt_ss", "login_status",
        }
        return any(
            str(value or "").strip()
            for name, value in cookie_dict.items()
            if str(name or "").lower() in authenticated_cookie_names
        )

    async def _visible_sms_input(self, allow_inactive: bool = False):
        selector = (
            "input[placeholder*='验证码'], input[aria-label*='验证码'], "
            "input[name*='code'], input[class*='code'], input[autocomplete='one-time-code']"
        )
        candidates = []
        for frame in [self.context_page, *self.context_page.frames]:
            if not allow_inactive and not await self._sms_mode_active(frame):
                continue
            locator = frame.locator(selector)
            for index in range(await locator.count()):
                candidate = locator.nth(index)
                try:
                    if await candidate.is_visible():
                        context_text = await candidate.evaluate(
                            """el => {
                                let node = el;
                                for (let index = 0; index < 6 && node; index += 1, node = node.parentElement) {
                                    const text = String(node.innerText || '').trim();
                                    if (text.includes('短信已发送至') || text.includes('重新发送')) return text;
                                }
                                return '';
                            }"""
                        )
                        score = 10 if "短信已发送至" in context_text else 8 if "重新发送" in context_text else 0
                        candidates.append((score, len(candidates), candidate))
                except Exception:
                    continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]

    async def _sms_mode_active(self, frame) -> bool:
        """The QR dialog keeps the SMS form in the DOM; only count it when its tab is active."""
        try:
            tab = frame.locator("span:text-is('验证码登录')").first
            if not await tab.count():
                return False
            parent = tab.locator("..").first
            classes = str(await parent.get_attribute("class") or "")
            return "qXy4xfTo" in classes
        except Exception:
            return False

    async def _choose_sms_verification(self) -> bool:
        selector = (
            "button:has-text('验证码验证'), button:has-text('短信验证'), "
            "[role='button']:has-text('验证码验证'), [role='button']:has-text('短信验证'), "
            "div[role='button']:has-text('验证码验证'), div[role='button']:has-text('短信验证')"
        )
        for frame in [self.context_page, *self.context_page.frames]:
            locator = frame.locator(selector)
            for index in range(await locator.count()):
                candidate = locator.nth(index)
                try:
                    if await candidate.is_visible():
                        await candidate.click(timeout=5000)
                        return True
                except Exception:
                    continue
        return False

    async def _submit_sms_verification(self) -> bool:
        """Click Douyin's verification control after filling the SMS code."""
        selector = (
            "button:text-is('验证'), [role='button']:text-is('验证'), "
            "div:text-is('验证'), span:text-is('验证')"
        )
        for frame in [self.context_page, *self.context_page.frames]:
            locator = frame.locator(selector)
            for index in range(await locator.count()):
                try:
                    candidate = locator.nth(index)
                    if await candidate.is_visible() and await candidate.is_enabled():
                        await candidate.click(timeout=5000)
                        return True
                except Exception:
                    continue
        return False

    async def _visible_identity_options(self):
        options = (
            ("receive_sms", "接收短信验证码"),
            ("send_sms", "发送短信验证"),
        )
        visible = []
        for frame in [self.context_page, *self.context_page.frames]:
            for value, label in options:
                locator = frame.locator(f"text={label}")
                for index in range(await locator.count()):
                    try:
                        if await locator.nth(index).is_visible():
                            visible.append({"value": value, "label": label})
                            break
                    except Exception:
                        continue
        return list({item["value"]: item for item in visible}.values())

    async def _choose_identity_option(self, action: str) -> bool:
        labels = {
            "receive_sms": "接收短信验证码",
            "send_sms": "发送短信验证",
        }
        label = labels.get(action)
        if not label:
            return False
        for frame in [self.context_page, *self.context_page.frames]:
            locator = frame.locator(f"text={label}")
            for index in range(await locator.count()):
                try:
                    candidate = locator.nth(index)
                    if await candidate.is_visible():
                        await candidate.click(timeout=5000)
                        return True
                except Exception:
                    continue
        return False

    async def _verification_choice_visible(self) -> bool:
        selector = (
            "button:has-text('验证码验证'), [role='button']:has-text('验证码验证'), "
            "div[role='button']:has-text('验证码验证')"
        )
        for frame in [self.context_page, *self.context_page.frames]:
            locator = frame.locator(selector)
            for index in range(await locator.count()):
                try:
                    if await locator.nth(index).is_visible():
                        return True
                except Exception:
                    continue
        return False

    async def wait_for_qrcode_login(self):
        """Wait for QR completion and handle the optional SMS challenge."""
        started_at = time.monotonic()
        verification_announced = False
        identity_announced = False
        code_applied = ""
        selected_action = ""
        while time.monotonic() - started_at < 600:
            if await self._has_login_state():
                return

            request = utils.read_login_verification_request()
            requested_action = str(request.get("action") or "").strip()
            if requested_action and requested_action != selected_action:
                if await self._choose_identity_option(requested_action):
                    selected_action = requested_action
                    utils.clear_login_verification_code()
                    utils.emit_login_verification("sms", "验证方式已选择，请输入抖音短信验证码")
                    verification_announced = True

            identity_options = await self._visible_identity_options()
            sms_input = await self._visible_sms_input(allow_inactive=bool(selected_action))
            page_text_parts = []
            for frame in [self.context_page, *self.context_page.frames]:
                try:
                    page_text_parts.append(await frame.locator("body").inner_text(timeout=1000))
                except Exception:
                    continue
            page_text = "\n".join(page_text_parts)
            if identity_options and not selected_action:
                if not identity_announced:
                    utils.emit_login_verification(
                        "identity",
                        "抖音需要完成身份验证，请选择一种验证方式",
                        identity_options,
                    )
                    utils.logger.info("[DouYinLogin.login_by_qrcode] Identity verification options are required")
                    identity_announced = True
                await asyncio.sleep(0.5)
                continue

            challenge_visible = bool(sms_input) or await self._verification_choice_visible() or any(
                marker in page_text for marker in ("请输入验证码", "输入验证码", "短信验证码已发送", "验证手机号")
            )
            if challenge_visible:
                choice_clicked = await self._choose_sms_verification()
                sms_input = sms_input or await self._visible_sms_input(allow_inactive=bool(selected_action or choice_clicked))
                if not sms_input and not choice_clicked and "验证手机号" not in page_text:
                    await asyncio.sleep(0.5)
                    continue
                if not verification_announced:
                    utils.emit_login_verification("sms", "抖音需要手机短信验证码，请在下方输入验证码")
                    utils.logger.info("[DouYinLogin.login_by_qrcode] SMS verification is required; waiting for code from the workbench")
                    verification_announced = True

                code = utils.read_login_verification_code()
                if code and code != code_applied and sms_input:
                    await sms_input.fill(code)
                    submitted = await self._submit_sms_verification()
                    if not submitted:
                        try:
                            await sms_input.press("Enter")
                            submitted = True
                        except Exception:
                            submitted = False
                    utils.logger.info(
                        f"[DouYinLogin.login_by_qrcode] SMS verification code filled; submit={'clicked' if submitted else 'not_found'}"
                    )
                    utils.clear_login_verification_code()
                    code_applied = code
                    await asyncio.sleep(1)
                await asyncio.sleep(0.5)
                continue
            await asyncio.sleep(1)

    async def login_by_mobile(self):
        utils.logger.info("[DouYinLogin.login_by_mobile] Begin login douyin by mobile ...")
        mobile_tap_ele = self.context_page.locator("xpath=//li[text() = '验证码登录']")
        await mobile_tap_ele.click()
        await self.context_page.wait_for_selector("xpath=//article[@class='web-login-mobile-code']")
        mobile_input_ele = self.context_page.locator("xpath=//input[@placeholder='手机号']")
        await mobile_input_ele.fill(self.login_phone)
        await asyncio.sleep(0.5)
        send_sms_code_btn = self.context_page.locator("xpath=//span[text() = '获取验证码']")
        await send_sms_code_btn.click()

        # Check if there is slider verification
        await self.check_page_display_slider(move_step=10, slider_level="easy")
        cache_client = CacheFactory.create_cache(config.CACHE_TYPE_MEMORY)
        max_get_sms_code_time = 60 * 2  # Maximum time to get verification code is 2 minutes
        while max_get_sms_code_time > 0:
            utils.logger.info(f"[DouYinLogin.login_by_mobile] get douyin sms code from redis remaining time {max_get_sms_code_time}s ...")
            await asyncio.sleep(1)
            sms_code_key = f"dy_{self.login_phone}"
            sms_code_value = cache_client.get(sms_code_key)
            if not sms_code_value:
                max_get_sms_code_time -= 1
                continue

            sms_code_input_ele = self.context_page.locator("xpath=//input[@placeholder='请输入验证码']")
            await sms_code_input_ele.fill(value=sms_code_value.decode())
            await asyncio.sleep(0.5)
            submit_btn_ele = self.context_page.locator("xpath=//button[@class='web-login-button']")
            await submit_btn_ele.click()  # Click login
            # todo ... should also check the correctness of the verification code, it may be incorrect
            break

    async def check_page_display_slider(self, move_step: int = 10, slider_level: str = "easy"):
        """
        Check if slider verification appears on the page
        :return:
        """
        # Wait for slider verification to appear
        back_selector = "#captcha-verify-image"
        try:
            await self.context_page.wait_for_selector(selector=back_selector, state="visible", timeout=30 * 1000)
        except PlaywrightTimeoutError:  # No slider verification, return directly
            return

        gap_selector = 'xpath=//*[@id="captcha_container"]/div/div[2]/img[2]'
        max_slider_try_times = 20
        slider_verify_success = False
        while not slider_verify_success:
            if max_slider_try_times <= 0:
                utils.logger.error("[DouYinLogin.check_page_display_slider] slider verify failed ...")
                sys.exit()
            try:
                await self.move_slider(back_selector, gap_selector, move_step, slider_level)
                await asyncio.sleep(1)

                # If the slider is too slow or verification failed, it will prompt "The operation is too slow", click the refresh button here
                page_content = await self.context_page.content()
                if "操作过慢" in page_content or "提示重新操作" in page_content:
                    utils.logger.info("[DouYinLogin.check_page_display_slider] slider verify failed, retry ...")
                    await self.context_page.click(selector="//a[contains(@class, 'secsdk_captcha_refresh')]")
                    continue

                # After successful sliding, wait for the slider to disappear
                await self.context_page.wait_for_selector(selector=back_selector, state="hidden", timeout=1000)
                # If the slider disappears, it means the verification is successful, break the loop. If not, it means the verification failed, the above line will throw an exception and be caught to continue the loop
                utils.logger.info("[DouYinLogin.check_page_display_slider] slider verify success ...")
                slider_verify_success = True
            except Exception as e:
                utils.logger.error(f"[DouYinLogin.check_page_display_slider] slider verify failed, error: {e}")
                await asyncio.sleep(1)
                max_slider_try_times -= 1
                utils.logger.info(f"[DouYinLogin.check_page_display_slider] remaining slider try times: {max_slider_try_times}")
                continue

    async def move_slider(self, back_selector: str, gap_selector: str, move_step: int = 10, slider_level="easy"):
        """
        Move the slider to the right to complete the verification
        :param back_selector: Selector for the slider verification background image
        :param gap_selector:  Selector for the slider verification slider
        :param move_step: Controls the ratio of single movement speed, default is 1, meaning the distance moves in 0.1 seconds no matter how far, larger value means slower
        :param slider_level: Slider difficulty easy hard, corresponding to the slider for mobile verification code and the slider in the middle of verification code
        :return:
        """

        # get slider background image
        slider_back_elements = await self.context_page.wait_for_selector(
            selector=back_selector,
            timeout=1000 * 10,  # wait 10 seconds
        )
        slide_back = str(await slider_back_elements.get_property("src")) # type: ignore

        # get slider gap image
        gap_elements = await self.context_page.wait_for_selector(
            selector=gap_selector,
            timeout=1000 * 10,  # wait 10 seconds
        )
        gap_src = str(await gap_elements.get_property("src")) # type: ignore

        # Identify slider position
        slide_app = utils.Slide(gap=gap_src, bg=slide_back)
        distance = slide_app.discern()

        # Get movement trajectory
        tracks = utils.get_tracks(distance, slider_level)
        new_1 = tracks[-1] - (sum(tracks) - distance)
        tracks.pop()
        tracks.append(new_1)

        # Drag slider to specified position according to trajectory
        element = await self.context_page.query_selector(gap_selector)
        bounding_box = await element.bounding_box() # type: ignore

        await self.context_page.mouse.move(bounding_box["x"] + bounding_box["width"] / 2, # type: ignore
                                           bounding_box["y"] + bounding_box["height"] / 2) # type: ignore
        # Get x coordinate center position
        x = bounding_box["x"] + bounding_box["width"] / 2 # type: ignore
        # Simulate sliding operation
        await element.hover() # type: ignore
        await self.context_page.mouse.down()

        for track in tracks:
            # Loop mouse movement according to trajectory
            # steps controls the ratio of single movement speed, default is 1, meaning the distance moves in 0.1 seconds no matter how far, larger value means slower
            await self.context_page.mouse.move(x + track, 0, steps=move_step)
            x += track
        await self.context_page.mouse.up()

    async def login_by_cookies(self):
        utils.logger.info("[DouYinLogin.login_by_cookies] Begin login douyin by cookie ...")
        for key, value in utils.convert_str_cookie_to_dict(self.cookie_str).items():
            await self.browser_context.add_cookies([{
                'name': key,
                'value': value,
                'domain': ".douyin.com",
                'path': "/"
            }])
