"""B 站网页端公开接口的轻量封装：热门、排行榜、搜索、视频详情、评论、字幕。

只用匿名可访问的接口，带简易限速；配置 BILI_COOKIE 后能拿到字幕、额度也更宽松。
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import DEFAULT_UA

BVID_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AID_RE = re.compile(r"(?:^|[/\s])av(\d+)", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

API_ROOT = "https://api.bilibili.com"

# 412 风控常见的诱因是 UA / buvid 指纹，换一套再试往往能过；仍失败就交给调用方降级。
RISK_RETRY_DELAYS: tuple[float, ...] = (1.5, 4.0)
FALLBACK_USER_AGENTS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)


class BiliError(RuntimeError):
    pass


class BiliRiskControl(BiliError):
    """B 站返回 HTTP 412 时的风控错误，调用方可以据此降级重试。"""


@dataclass(slots=True)
class HistoryItem:
    bvid: str
    title: str
    author: str = ""
    cover: str = ""
    duration: int = 0
    progress: int = 0
    view_at: int = 0

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}"

    @property
    def watched_seconds(self) -> int:
        # B 站历史里 progress = -1 表示已看完
        return self.duration if self.progress < 0 else self.progress

    def to_dict(self) -> dict[str, Any]:
        return {
            "bvid": self.bvid,
            "title": self.title,
            "up": self.author,
            "url": self.url,
            "duration": format_duration(self.duration),
            "watched_seconds": self.progress,
            "viewed_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.view_at)) if self.view_at else "",
        }


@dataclass(slots=True)
class Video:
    bvid: str
    aid: int = 0
    cid: int = 0
    title: str = ""
    author: str = ""
    mid: int = 0
    cover: str = ""
    description: str = ""
    duration: int = 0
    pubdate: int = 0
    stats: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}"

    def to_dict(self, *, include_description: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "bvid": self.bvid,
            "title": self.title,
            "up": self.author,
            "url": self.url,
            "duration": format_duration(self.duration),
            "播放": self.stats.get("view"),
            "点赞": self.stats.get("like"),
        }
        if self.pubdate:
            data["发布时间"] = time.strftime("%Y-%m-%d", time.localtime(self.pubdate))
        if include_description and self.description:
            data["简介"] = self.description[:240]
        if self.tags:
            data["标签"] = self.tags[:8]
        return {k: v for k, v in data.items() if v not in (None, "", [])}

    def brief(self) -> str:
        return f"{self.title}（UP：{self.author}）{self.url}"


def format_duration(seconds: int) -> str:
    seconds = max(int(seconds or 0), 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_duration(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return 0
    parts = value.strip().split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return 0
    total = 0
    for number in numbers:
        total = total * 60 + number
    return total


def clean_text(value: Any, limit: int = 240) -> str:
    text = html.unescape(TAG_RE.sub("", str(value or "")))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def extract_bvid(text: str) -> str | None:
    match = BVID_RE.search(text or "")
    return match.group(1) if match else None


def extract_aid(text: str) -> int | None:
    match = AID_RE.search(text or "")
    return int(match.group(1)) if match else None


def normalize_cover(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return url.replace("http://", "https://", 1)


class BilibiliClient:
    def __init__(
        self,
        *,
        cookie: str = "",
        user_agent: str = DEFAULT_UA,
        min_interval: float = 0.6,
        search_min_interval: float = 2.0,
        timeout: float = 15.0,
        http_client: httpx.AsyncClient | None = None,
        risk_retry_delays: tuple[float, ...] = RISK_RETRY_DELAYS,
    ) -> None:
        self.cookie = cookie.strip()
        self.user_agent = user_agent
        self.risk_retry_delays = tuple(max(delay, 0.0) for delay in risk_retry_delays)
        self.min_interval = max(min_interval, 0.0)
        self.search_min_interval = max(search_min_interval, 0.0)
        self._last_search = 0.0
        self._search_lock = asyncio.Lock()
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout,
            headers={
                "User-Agent": user_agent,
                "Referer": "https://www.bilibili.com/",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        self._owns_client = http_client is None
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._mixin_key: str | None = None
        self._mixin_key_at = 0.0
        self._buvid = ""
        self._buvid_loaded = False
        self._video_cache: dict[str, Video] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ HTTP
    async def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = False,
        allow_codes: tuple[int, ...] = (),
        referer: str | None = None,
        risk_retries: int | None = None,
    ) -> dict[str, Any]:
        if signed:
            params = await self._sign(params or {})
        retries = len(self.risk_retry_delays) if risk_retries is None else max(risk_retries, 0)

        for attempt in range(retries + 1):
            response = await self._send(path, params, referer=referer, attempt=attempt)
            if response.status_code != 412:
                break
            # 换指纹再试：清掉 buvid，下一次会重新取一份新的。
            self._buvid_loaded = False
            self._buvid = ""
            if attempt >= retries:
                raise BiliRiskControl(
                    "B 站风控了（HTTP 412），歇一会儿再试；配置 BILI_COOKIE 会稳一些"
                )
            await asyncio.sleep(self.risk_retry_delays[attempt])

        if response.status_code != 200:
            raise BiliError(f"B 站返回 HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise BiliError("B 站返回的不是 JSON，可能被风控了") from exc

        code = int(data.get("code", -1))
        if code != 0 and code not in allow_codes:
            message = data.get("message") or data.get("msg") or "未知错误"
            raise BiliError(f"B 站接口报错（{code} {message}）")
        return data

    def _user_agent_for(self, attempt: int) -> str:
        if attempt == 0 or not FALLBACK_USER_AGENTS:
            return self.user_agent
        return FALLBACK_USER_AGENTS[(attempt - 1) % len(FALLBACK_USER_AGENTS)]

    async def _send(
        self,
        path: str,
        params: dict[str, Any] | None,
        *,
        referer: str | None,
        attempt: int,
    ) -> httpx.Response:
        await self._ensure_buvid()
        cookie = "; ".join(part for part in (self.cookie, self._buvid) if part)
        headers = {"User-Agent": self._user_agent_for(attempt)}
        if cookie:
            headers["Cookie"] = cookie
        if referer:
            headers["Referer"] = referer

        async with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._client.get(API_ROOT + path, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise BiliError(f"请求 B 站失败：{exc}") from exc
            finally:
                self._last_request = time.monotonic()

    async def _ensure_buvid(self) -> None:
        """匿名请求也需要 buvid3/4，否则搜索接口只返回风控凭证。"""
        if self._buvid_loaded:
            return
        self._buvid_loaded = True
        try:
            response = await self._client.get(
                API_ROOT + "/x/frontend/finger/spi",
                headers={"User-Agent": self.user_agent, "Referer": "https://www.bilibili.com/"},
            )
            data = response.json().get("data") or {}
        except (httpx.HTTPError, ValueError):
            self._buvid_loaded = False
            return
        parts = []
        if data.get("b_3"):
            parts.append(f"buvid3={data['b_3']}")
        if data.get("b_4"):
            parts.append(f"buvid4={data['b_4']}")
        if parts:
            parts.append(f"b_nut={int(time.time())}")
        self._buvid = "; ".join(parts)

    async def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        mixin_key = await self._get_mixin_key()
        cleaned = {
            key: "".join(ch for ch in str(value) if ch not in "!'()*")
            for key, value in params.items()
            if value is not None
        }
        cleaned["wts"] = int(time.time())
        query = urlencode(sorted(cleaned.items()))
        cleaned["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
        return cleaned

    async def _get_mixin_key(self) -> str:
        if self._mixin_key and time.monotonic() - self._mixin_key_at < 3600:
            return self._mixin_key

        data = await self._request("/x/web-interface/nav", allow_codes=(-101,))
        wbi = (data.get("data") or {}).get("wbi_img") or {}
        img_key = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
        sub_key = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
        if not img_key or not sub_key:
            raise BiliError("拿不到 B 站的 wbi 签名密钥，稍后再试")
        raw = img_key + sub_key
        self._mixin_key = "".join(raw[index] for index in MIXIN_KEY_ENC_TAB if index < len(raw))[:32]
        self._mixin_key_at = time.monotonic()
        return self._mixin_key

    # ------------------------------------------------------------------ 列表
    async def hot(self, limit: int = 20) -> list[Video]:
        data = await self._request("/x/web-interface/popular", {"ps": min(limit, 50), "pn": 1})
        return [self._video_from_item(item) for item in (data.get("data") or {}).get("list") or []]

    async def ranking(self, limit: int = 20) -> list[Video]:
        data = await self._request("/x/web-interface/ranking/v2", {"rid": 0, "type": "all"})
        items = (data.get("data") or {}).get("list") or []
        return [self._video_from_item(item) for item in items[:limit]]

    async def search(self, keyword: str, limit: int = 10) -> list[Video]:
        keyword = keyword.strip()
        if not keyword:
            return []

        async with self._search_lock:
            wait = self.search_min_interval - (time.monotonic() - self._last_search)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_search = time.monotonic()

            try:
                payload = await self._search_payload(keyword, signed=True)
            except BiliRiskControl:
                # HTTP 412：清掉指纹，换不需要签名的老接口再试一次
                self._buvid_loaded = False
                self._buvid = ""
                payload = await self._search_payload(
                    keyword, signed=False, risk_retries=0
                )
            if not (payload.get("result") or []) and payload.get("v_voucher"):
                # 风控凭证：换一套 buvid，再用不需要签名的老接口兜底一次
                self._buvid_loaded = False
                self._buvid = ""
                payload = await self._search_payload(keyword, signed=False)
            if not (payload.get("result") or []) and payload.get("v_voucher"):
                raise BiliError("B 站搜索触发了风控验证，等几分钟再试；配置 BILI_COOKIE 会稳一些")

        videos = []
        for item in payload.get("result") or []:
            if item.get("type") and item.get("type") != "video":
                continue
            videos.append(self._video_from_item(item))
            if len(videos) >= limit:
                break
        return videos

    async def _search_payload(
        self, keyword: str, *, signed: bool, risk_retries: int | None = None
    ) -> dict[str, Any]:
        path = "/x/web-interface/wbi/search/type" if signed else "/x/web-interface/search/type"
        params: dict[str, Any] = {"search_type": "video", "keyword": keyword, "page": 1}
        if signed:
            params["page_size"] = 20
            params["platform"] = "pc"
        else:
            params["page_size"] = 20
        data = await self._request(
            path,
            params,
            signed=signed,
            referer="https://search.bilibili.com/all",
            risk_retries=risk_retries,
        )
        return data.get("data") or {}

    # ------------------------------------------------------------------ 详情
    async def video(self, id_or_url: str) -> Video:
        key = (id_or_url or "").strip()
        bvid = extract_bvid(key)
        aid = extract_aid(key)
        cache_key = bvid or (str(aid) if aid else key)
        if cache_key in self._video_cache:
            return self._video_cache[cache_key]

        params: dict[str, Any]
        if bvid:
            params = {"bvid": bvid}
        elif aid:
            params = {"aid": aid}
        elif key.isdigit():
            params = {"aid": int(key)}
        else:
            raise BiliError(f"没认出视频号：{key!r}，给我 BV 号、av 号或完整链接")

        data = await self._request("/x/web-interface/view", params)
        payload = data.get("data") or {}
        video = self._video_from_view(payload)
        await self._fill_tags(video)
        self._remember(video)
        return video

    async def history(self, limit: int = 20) -> list[HistoryItem]:
        """主人 B 站观看历史；必须配置 BILI_COOKIE（含 SESSDATA）。"""
        if not self.cookie:
            raise BiliError("读取观看历史需要先在 .env 配置 BILI_COOKIE（至少包含 SESSDATA）")
        data = await self._request(
            "/x/web-interface/history/cursor",
            {
                "ps": min(max(limit, 1), 30),
                "max": 0,
                "view_at": 0,
                "business": "",
            },
        )
        items = (data.get("data") or {}).get("list") or []
        result: list[HistoryItem] = []
        for item in items:
            history = item.get("history") or {}
            bvid = history.get("bvid") or item.get("bvid") or ""
            if not bvid:
                continue
            result.append(
                HistoryItem(
                    bvid=bvid,
                    title=clean_text(item.get("title") or item.get("long_title"), 120),
                    author=clean_text(item.get("author_name") or item.get("author"), 40),
                    cover=normalize_cover(item.get("cover") or ""),
                    duration=int(item.get("duration") or 0),
                    progress=int(item.get("progress") or 0),
                    view_at=int(item.get("view_at") or 0),
                )
            )
            if len(result) >= limit:
                break
        return result

    async def comments(self, id_or_url: str, limit: int = 8) -> list[dict[str, Any]]:
        video = await self.video(id_or_url)
        if not video.aid:
            return []
        data = await self._request(
            "/x/v2/reply",
            {"type": 1, "oid": video.aid, "ps": min(max(limit, 1), 20), "sort": 2},
        )
        replies = (data.get("data") or {}).get("replies") or []
        comments = []
        for reply in replies:
            content = (reply.get("content") or {}).get("message") or ""
            if not content:
                continue
            comments.append(
                {
                    "user": (reply.get("member") or {}).get("uname") or "",
                    "text": clean_text(content, 160),
                    "like": reply.get("like") or 0,
                }
            )
        return comments

    async def watch(
        self, id_or_url: str, *, comment_limit: int = 6, subtitle_chars: int = 1500
    ) -> dict[str, Any]:
        """「看视频」：标题、简介、标签，加上最高赞评论和（登录时）字幕。"""
        video = await self.video(id_or_url)
        result: dict[str, Any] = {"video": video.to_dict()}
        try:
            comments = await self.comments(video.bvid, comment_limit)
        except BiliError:
            comments = []
        if comments:
            result["热门评论"] = comments
        subtitle = await self._subtitle(video, max_chars=subtitle_chars)
        if subtitle:
            result["字幕节选"] = subtitle
        return result

    async def _fill_tags(self, video: Video) -> None:
        try:
            data = await self._request("/x/tag/archive/tags", {"bvid": video.bvid})
        except BiliError:
            return
        tags = [clean_text(item.get("tag_name"), 20) for item in data.get("data") or []]
        video.tags = [tag for tag in tags if tag]

    async def _subtitle(self, video: Video, max_chars: int = 1500) -> str:
        if not self.cookie or not video.cid:
            return ""
        try:
            data = await self._request(
                "/x/player/wbi/v2",
                {"bvid": video.bvid, "cid": video.cid},
                signed=True,
            )
        except BiliError:
            return ""
        subtitles = ((data.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        if not subtitles:
            return ""
        url = subtitles[0].get("subtitle_url") or ""
        if url.startswith("//"):
            url = "https:" + url
        if not url:
            return ""
        try:
            response = await self._client.get(url, headers={"User-Agent": self.user_agent})
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return ""
        lines = [
            clean_text(item.get("content"), 200)
            for item in payload.get("body") or []
            if item.get("content")
        ]
        return " ".join(lines)[: max(max_chars, 200)]

    # ------------------------------------------------------------------ 解析
    def _video_from_item(self, item: dict[str, Any]) -> Video:
        owner = item.get("owner") or {}
        stat = item.get("stat") or {}
        return Video(
            bvid=item.get("bvid") or "",
            aid=int(item.get("aid") or 0),
            title=clean_text(item.get("title"), 120),
            author=clean_text(item.get("author") or owner.get("name"), 40),
            mid=int(item.get("mid") or owner.get("mid") or 0),
            cover=normalize_cover(item.get("pic") or ""),
            description=clean_text(item.get("description") or item.get("desc"), 240),
            duration=parse_duration(item.get("duration")),
            pubdate=int(item.get("pubdate") or 0),
            stats={
                "view": stat.get("view", item.get("play")),
                "like": stat.get("like"),
                "danmaku": stat.get("danmaku", item.get("video_review")),
                "reply": stat.get("reply", item.get("review")),
            },
        )

    def _video_from_view(self, data: dict[str, Any]) -> Video:
        owner = data.get("owner") or {}
        stat = data.get("stat") or {}
        pages = data.get("pages") or []
        cid = int((pages[0] or {}).get("cid") or data.get("cid") or 0) if pages else int(data.get("cid") or 0)
        return Video(
            bvid=data.get("bvid") or "",
            aid=int(data.get("aid") or 0),
            cid=cid,
            title=clean_text(data.get("title"), 120),
            author=clean_text(owner.get("name"), 40),
            mid=int(owner.get("mid") or 0),
            cover=normalize_cover(data.get("pic") or ""),
            description=clean_text(data.get("desc"), 500),
            duration=int(data.get("duration") or 0),
            pubdate=int(data.get("pubdate") or 0),
            stats={
                "view": stat.get("view"),
                "like": stat.get("like"),
                "danmaku": stat.get("danmaku"),
                "reply": stat.get("reply"),
                "coin": stat.get("coin"),
                "favorite": stat.get("favorite"),
                "share": stat.get("share"),
            },
        )

    def _remember(self, video: Video) -> None:
        if not video.bvid:
            return
        self._video_cache[video.bvid] = video
        if video.aid:
            self._video_cache[str(video.aid)] = video
        if len(self._video_cache) > 200:
            for key in list(self._video_cache)[:50]:
                self._video_cache.pop(key, None)
