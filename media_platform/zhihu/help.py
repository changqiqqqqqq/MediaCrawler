# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/zhihu/help.py
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
import ast
import hashlib
import json
import random
import re
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from parsel import Selector

from constant import zhihu as zhihu_constant
from model.m_zhihu import ZhihuComment, ZhihuContent, ZhihuCreator
from tools import utils
from tools.crawler_util import extract_text_from_html
from tools.user_hash import anonymize_user_id, mask_nickname

ZHIHU_SGIN_JS = None
_SIGN_CONSTANTS = None


def _signed32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _unsigned32(value: int) -> int:
    return value & 0xFFFFFFFF


def _load_sign_constants():
    global _SIGN_CONSTANTS
    if _SIGN_CONSTANTS is not None:
        return _SIGN_CONSTANTS
    js_path = __file__
    js_file = __import__('pathlib').Path(js_path).resolve().parents[2] / "libs" / "zhihu.js"
    source = js_file.read_text(encoding="utf-8-sig")
    match = re.search(r"zk:\s*(\[.*?\]),\s*zb:\s*(\[.*?\])", source, re.S)
    if not match:
        raise RuntimeError("Zhihu signing constants are missing")
    _SIGN_CONSTANTS = (ast.literal_eval(match.group(1)), ast.literal_eval(match.group(2)))
    return _SIGN_CONSTANTS


def _zhihu_sign_python(url: str, cookies: str) -> Dict:
    """Compute Zhihu's request signature without requiring a JS runtime."""
    zk, zb = _load_sign_constants()
    init_str = "6fpLRqJO8M/c3jnYxFkUVC4ZIG12SiH=5v0mXDazWBTsuw7QetbKdoPyAl+hN9rgE"
    dc0_match = re.search(r"(?:^|;)\s*d_c0=([^;]+)", cookies or "")
    dc0 = dc0_match.group(1) if dc0_match else ""
    tc = "3_2.0aR_sn77yn6O92wOB8hPZnQr0EMYxc4f18wNBUgpTQ6nxERFZfTY0-4Lm-h3_tufIwJS8gcxTgJS_AuPZNcXCTwxI78YxEM20s4PGDwN8gGcYAupMWufIoLVqr4gxrRPOI0cY7HL8qun9g93mFukyigcmebS_FwOYPRP0E4rZUrN9DDom3hnynAUMnAVPF_PhaueTFH9fQL39OCCqYTxfb0rfi9wfPhSM6vxGDJo_rBHpQGNmBBLqPJHK2_w8C9eTVMO9Z9NOrMtfhGH_DgpM-BNM1DOxScLG3gg1Hre1FCXKQcXKkrSL1r9GWDXMk8wqBLNmbRH96BtOFqVZ7UYG3gC8D9cMS7Y9UrHLVCLZPJO8_CL_6GNCOg_zhJS8PbXmGTcBpgxfkieOPhNfthtf2gC_qD3YOce8nCwG2uwBOqeMoML9NBC1xb9yk6SuJhHLK7SM6LVfCve_3vLKlqcL6TxL_UosDvHLxrHmWgxBQ8Xs"
    md5_value = hashlib.md5(f"101_3_3.0+{url}+{dc0}+{tc}".encode()).hexdigest()

    def rotate(value: int, bits: int) -> int:
        unsigned = _unsigned32(value)
        return _signed32((unsigned << bits) | (unsigned >> (32 - bits)))

    def word_from_bytes(values, offset):
        return _signed32((_unsigned32(values[offset]) << 24) | (values[offset + 1] << 16) | (values[offset + 2] << 8) | values[offset + 3])

    def word_to_bytes(value):
        unsigned = _unsigned32(value)
        return [(unsigned >> 24) & 255, (unsigned >> 16) & 255, (unsigned >> 8) & 255, unsigned & 255]

    def transform(value):
        values = word_to_bytes(value)
        substituted = [zb[b] for b in values]
        word = word_from_bytes(substituted, 0)
        return _signed32(word ^ rotate(word, 2) ^ rotate(word, 10) ^ rotate(word, 18) ^ rotate(word, 24))

    def block(values):
        words = [word_from_bytes(values, i) for i in range(0, 16, 4)] + [0] * 32
        for index in range(32):
            words[index + 4] = _signed32(words[index] ^ transform(_signed32(words[index + 1] ^ words[index + 2] ^ words[index + 3] ^ zk[index])))
        output = []
        for index in (35, 34, 33, 32):
            output.extend(word_to_bytes(words[index]))
        return output

    offsets = [48, 53, 57, 48, 53, 51, 102, 55, 100, 49, 53, 101, 48, 49, 100, 55]
    init = [ord(char) for char in md5_value]
    init.insert(0, 0)
    init.insert(0, random.randrange(127))
    init.extend([14] * (48 - len(init)))
    first_input = [((value ^ offsets[index]) ^ 42) for index, value in enumerate(init[:16])]
    first = block(first_input)
    transformed = []
    state = first
    for offset in range(0, 32, 16):
        state = block([left ^ right for left, right in zip(init[16 + offset:32 + offset], state)])
        transformed.extend(state)
    encoded = first + transformed
    for index in range(47, -1, -4):
        encoded[index] ^= 58
    encoded.reverse()
    result = []
    for index in range(0, len(encoded), 3):
        value = encoded[index] | (encoded[index + 1] << 8) | (encoded[index + 2] << 16)
        result.extend([(value >> shift) & 63 for shift in (0, 6, 12, 18)])
    return {"x-zst-81": tc, "x-zse-96": "2.0_" + "".join(init_str[index] for index in result)}


def sign(url: str, cookies: str) -> Dict:
    """
    zhihu sign algorithm
    Args:
        url: request url with query string
        cookies: request cookies with d_c0 key

    Returns:

    """
    return _zhihu_sign_python(url, cookies)


class ZhihuExtractor:
    def __init__(self):
        pass

    def extract_contents_from_search(self, json_data: Dict) -> List[ZhihuContent]:
        """
        extract zhihu contents
        Args:
            json_data: zhihu json data

        Returns:

        """
        if not json_data:
            return []

        search_result: List[Dict] = json_data.get("data", [])
        search_result = [s_item for s_item in search_result if s_item.get("type") in ['search_result', 'zvideo']]
        return self._extract_content_list([sr_item.get("object") for sr_item in search_result if sr_item.get("object")])


    def _extract_content_list(self, content_list: List[Dict]) -> List[ZhihuContent]:
        """
        extract zhihu content list
        Args:
            content_list:

        Returns:

        """
        if not content_list:
            return []

        res: List[ZhihuContent] = []
        for content in content_list:
            if content.get("type") == zhihu_constant.ANSWER_NAME:
                res.append(self._extract_answer_content(content))
            elif content.get("type") == zhihu_constant.ARTICLE_NAME:
                res.append(self._extract_article_content(content))
            elif content.get("type") == zhihu_constant.VIDEO_NAME:
                res.append(self._extract_zvideo_content(content))
            else:
                continue
        return res

    def _extract_answer_content(self, answer: Dict) -> ZhihuContent:
        """
        extract zhihu answer content
        Args:
            answer: zhihu answer

        Returns:
        """
        res = ZhihuContent()
        res.content_id = str(answer.get("id") or "")
        res.content_type = answer.get("type")
        res.content_text = extract_text_from_html(answer.get("content", ""))
        res.question_id = str(answer.get("question", {}).get("id") or "")
        res.content_url = f"{zhihu_constant.ZHIHU_URL}/question/{res.question_id}/answer/{res.content_id}"
        res.title = extract_text_from_html(answer.get("title", ""))
        res.desc = extract_text_from_html(answer.get("description", "") or answer.get("excerpt", ""))
        res.created_time = answer.get("created_time")
        res.updated_time = answer.get("updated_time")
        res.voteup_count = answer.get("voteup_count", 0)
        res.comment_count = answer.get("comment_count", 0)

        # extract author info
        author_info = self._extract_content_or_comment_author(answer.get("author"))
        res.creator_hash = author_info.creator_hash
        res.user_nickname = author_info.user_nickname
        return res

    def _extract_article_content(self, article: Dict) -> ZhihuContent:
        """
        extract zhihu article content
        Args:
            article: zhihu article

        Returns:

        """
        res = ZhihuContent()
        res.content_id = str(article.get("id") or "")
        res.content_type = article.get("type")
        res.content_text = extract_text_from_html(article.get("content"))
        res.content_url = f"{zhihu_constant.ZHIHU_ZHUANLAN_URL}/p/{res.content_id}"
        res.title = extract_text_from_html(article.get("title"))
        res.desc = extract_text_from_html(article.get("excerpt"))
        res.created_time = article.get("created_time", 0) or article.get("created", 0)
        res.updated_time = article.get("updated_time", 0) or article.get("updated", 0)
        res.voteup_count = article.get("voteup_count", 0)
        res.comment_count = article.get("comment_count", 0)

        # extract author info
        author_info = self._extract_content_or_comment_author(article.get("author"))
        res.creator_hash = author_info.creator_hash
        res.user_nickname = author_info.user_nickname
        return res

    def _extract_zvideo_content(self, zvideo: Dict) -> ZhihuContent:
        """
        extract zhihu zvideo content
        Args:
            zvideo:

        Returns:

        """
        res = ZhihuContent()
        res.content_id = str(zvideo.get("id") or "")
        res.content_type = zvideo.get("type")

        if "video" in zvideo and isinstance(zvideo.get("video"), dict): # This indicates data from the creator's homepage video list API
            res.content_url = f"{zhihu_constant.ZHIHU_URL}/zvideo/{res.content_id}"
            res.created_time = zvideo.get("published_at")
            res.updated_time = zvideo.get("updated_at")
        else:
            res.content_url = zvideo.get("video_url")
            res.created_time = zvideo.get("created_at")
        res.title = extract_text_from_html(zvideo.get("title"))
        res.desc = extract_text_from_html(zvideo.get("description"))
        res.voteup_count = zvideo.get("voteup_count")
        res.comment_count = zvideo.get("comment_count")

        # extract author info
        author_info = self._extract_content_or_comment_author(zvideo.get("author"))
        res.creator_hash = author_info.creator_hash
        res.user_nickname = author_info.user_nickname
        return res

    @staticmethod
    def _extract_content_or_comment_author(author: Dict) -> ZhihuCreator:
        """
        extract zhihu author
        Args:
            author:

        Returns:

        """
        res = ZhihuCreator()
        try:
            if not author:
                return res
            if not author.get("id"):
                author = author.get("member")
            res.creator_hash = anonymize_user_id(author.get("id"))
            res.user_nickname = mask_nickname(author.get("name"))

        except Exception as e :
            utils.logger.warning(
                f"[ZhihuExtractor._extract_content_or_comment_author] User Maybe Blocked. {e}"
            )
        return res

    def extract_comments(self, page_content: ZhihuContent, comments: List[Dict]) -> List[ZhihuComment]:
        """
        extract zhihu comments
        Args:
            page_content: zhihu content object
            comments: zhihu comments

        Returns:

        """
        if not comments:
            return []
        res: List[ZhihuComment] = []
        for comment in comments:
            if comment.get("type") != "comment":
                continue
            res.append(self._extract_comment(page_content, comment))
        return res

    def _extract_comment(self, page_content: ZhihuContent, comment: Dict) -> ZhihuComment:
        """
        extract zhihu comment
        Args:
            page_content: comment with content object
            comment: zhihu comment

        Returns:

        """
        res = ZhihuComment()
        res.comment_id = str(comment.get("id", ""))
        res.parent_comment_id = comment.get("reply_comment_id")
        res.content = extract_text_from_html(comment.get("content"))
        res.publish_time = comment.get("created_time")
        res.sub_comment_count = comment.get("child_comment_count")
        res.like_count = comment.get("like_count") if comment.get("like_count") else 0
        res.dislike_count = comment.get("dislike_count") if comment.get("dislike_count") else 0
        res.content_id = page_content.content_id
        res.content_type = page_content.content_type

        # extract author info
        author_info = self._extract_content_or_comment_author(comment.get("author"))
        res.creator_hash = author_info.creator_hash
        res.user_nickname = author_info.user_nickname
        return res

    @staticmethod
    def _extract_comment_ip_location(comment_tags: List[Dict]) -> str:
        """
        extract comment ip location
        Args:
            comment_tags:

        Returns:

        """
        if not comment_tags:
            return ""

        for ct in comment_tags:
            if ct.get("type") == "ip_info":
                return ct.get("text")

        return ""

    @staticmethod
    def extract_offset(paging_info: Dict) -> str:
        """
        extract offset
        Args:
            paging_info:

        Returns:

        """
        # https://www.zhihu.com/api/v4/comment_v5/zvideos/1424368906836807681/root_comment?limit=10&offset=456770961_10125996085_0&order_by=score
        next_url = paging_info.get("next")
        if not next_url:
            return ""

        parsed_url = urlparse(next_url)
        query_params = parse_qs(parsed_url.query)
        offset = query_params.get('offset', [""])[0]
        return offset

    @staticmethod
    def _foramt_gender_text(gender: int) -> str:
        """
        format gender text
        Args:
            gender:

        Returns:

        """
        if gender == 1:
            return "Male"
        elif gender == 0:
            return "Female"
        else:
            return "Unknown"


    def extract_creator(self, user_url_token: str, html_content: str) -> Optional[ZhihuCreator]:
        """
        extract zhihu creator
        Args:
            user_url_token : zhihu creator url token
            html_content: zhihu creator html content

        Returns:

        """
        if not html_content:
            return None

        js_init_data = Selector(text=html_content).xpath("//script[@id='js-initialData']/text()").get(default="").strip()
        if not js_init_data:
            return None

        js_init_data_dict: Dict = json.loads(js_init_data)
        users_info: Dict = js_init_data_dict.get("initialState", {}).get("entities", {}).get("users", {})
        if not users_info:
            return None

        creator_info: Dict = users_info.get(user_url_token)
        if not creator_info:
            return None

        res = ZhihuCreator()
        res.creator_hash = anonymize_user_id(creator_info.get("id"))
        res.user_nickname = mask_nickname(creator_info.get("name"))
        res.follows = creator_info.get("followingCount")
        res.fans = creator_info.get("followerCount")
        res.anwser_count = creator_info.get("answerCount")
        res.video_count = creator_info.get("zvideoCount")
        res.question_count = creator_info.get("questionCount")
        res.article_count = creator_info.get("articlesCount")
        res.column_count = creator_info.get("columnsCount")
        res.get_voteup_count = creator_info.get("voteupCount")
        return res


    def extract_content_list_from_creator(self, anwser_list: List[Dict]) -> List[ZhihuContent]:
        """
        extract content list from creator
        Args:
            anwser_list:

        Returns:

        """
        if not anwser_list:
            return []

        return self._extract_content_list(anwser_list)




    def extract_answer_content_from_html(self, html_content: str) -> Optional[ZhihuContent]:
        """
        extract zhihu answer content from html
        Args:
            html_content:

        Returns:

        """
        js_init_data: str = Selector(text=html_content).xpath("//script[@id='js-initialData']/text()").get(default="")
        if not js_init_data:
            return None
        json_data: Dict = json.loads(js_init_data)
        answer_info: Dict = json_data.get("initialState", {}).get("entities", {}).get("answers", {})
        if not answer_info:
            return None

        return self._extract_answer_content(answer_info.get(list(answer_info.keys())[0]))

    def extract_article_content_from_html(self, html_content: str) -> Optional[ZhihuContent]:
        """
        extract zhihu article content from html
        Args:
            html_content:

        Returns:

        """
        js_init_data: str = Selector(text=html_content).xpath("//script[@id='js-initialData']/text()").get(default="")
        if not js_init_data:
            return None
        json_data: Dict = json.loads(js_init_data)
        article_info: Dict = json_data.get("initialState", {}).get("entities", {}).get("articles", {})
        if not article_info:
            return None

        return self._extract_article_content(article_info.get(list(article_info.keys())[0]))

    def extract_zvideo_content_from_html(self, html_content: str) -> Optional[ZhihuContent]:
        """
        extract zhihu zvideo content from html
        Args:
            html_content:

        Returns:

        """
        js_init_data: str = Selector(text=html_content).xpath("//script[@id='js-initialData']/text()").get(default="")
        if not js_init_data:
            return None
        json_data: Dict = json.loads(js_init_data)
        zvideo_info: Dict = json_data.get("initialState", {}).get("entities", {}).get("zvideos", {})
        users: Dict = json_data.get("initialState", {}).get("entities", {}).get("users", {})
        if not zvideo_info:
            return None

        # handler user info and video info
        video_detail_info: Dict = zvideo_info.get(list(zvideo_info.keys())[0])
        if not video_detail_info:
            return None
        if isinstance(video_detail_info.get("author"), str):
            author_name: str = video_detail_info.get("author")
            video_detail_info["author"] = users.get(author_name)

        return self._extract_zvideo_content(video_detail_info)


def judge_zhihu_url(note_detail_url: str) -> str:
    """
    judge zhihu url type
    Args:
        note_detail_url:
            eg1: https://www.zhihu.com/question/123456789/answer/123456789 # answer
            eg2: https://www.zhihu.com/p/123456789 # article
            eg3: https://www.zhihu.com/zvideo/123456789 # zvideo

    Returns:

    """
    if "/answer/" in note_detail_url:
        return zhihu_constant.ANSWER_NAME
    elif "/p/" in note_detail_url:
        return zhihu_constant.ARTICLE_NAME
    elif "/zvideo/" in note_detail_url:
        return zhihu_constant.VIDEO_NAME
    else:
        return ""
