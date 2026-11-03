from __future__ import annotations

import httpx
import pytest

from dafeiyu.bilibili import (
    BilibiliClient,
    BiliError,
    BiliRiskControl,
    extract_aid,
    extract_bvid,
    format_duration,
    parse_duration,
    summarize_danmaku,
)

IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"

POPULAR_ITEM = {
    "aid": 111,
    "bvid": "BV1xx411c7mD",
    "title": "今天的大肥鱼",
    "desc": "一条鱼",
    "pic": "//i1.hdslb.com/cover.jpg",
    "owner": {"mid": 5, "name": "养鱼人"},
    "stat": {"view": 1000, "like": 100, "danmaku": 8, "reply": 9},
    "duration": 754,
    "pubdate": 1760000000,
}

SEARCH_ITEM = {
    "type": "video",
    "aid": 222,
    "bvid": "BV1yy411c7mE",
    "title": '<em class="keyword">大肥鱼</em> &amp; 朋友',
    "author": "UP二号",
    "play": 500,
    "description": "搜索结果",
    "pic": "http://i0.hdslb.com/x.jpg",
    "duration": "12:34",
    "pubdate": 1760000001,
}


def make_client(
    handler, *, cookie: str = "", risk_retry_delays: tuple[float, ...] = ()
) -> tuple[BilibiliClient, httpx.AsyncClient]:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/frontend/finger/spi":
            return httpx.Response(200, json={"code": 0, "data": {"b_3": "b3", "b_4": "b4"}})
        return handler(request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    client = BilibiliClient(
        cookie=cookie,
        http_client=http_client,
        min_interval=0,
        search_min_interval=0,
        risk_retry_delays=risk_retry_delays,
    )
    return client, http_client


def test_id_helpers() -> None:
    assert extract_bvid("看这个 https://www.bilibili.com/video/BV1xx411c7mD?p=2") == "BV1xx411c7mD"
    assert extract_bvid("没有号") is None
    assert extract_aid("av12345") == 12345
    assert extract_aid("BV1xx411c7mD") is None
    assert format_duration(754) == "12:34"
    assert format_duration(3671) == "1:01:11"
    assert parse_duration("12:34") == 754
    assert parse_duration(30) == 30
    assert parse_duration("bad") == 0


async def test_hot_and_ranking() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/web-interface/popular":
            assert request.url.params["ps"] == "5"
            return httpx.Response(200, json={"code": 0, "data": {"list": [POPULAR_ITEM]}})
        if request.url.path == "/x/web-interface/ranking/v2":
            return httpx.Response(200, json={"code": 0, "data": {"list": [POPULAR_ITEM]}})
        raise AssertionError(request.url)

    client, http_client = make_client(handler)
    try:
        hot = await client.hot(limit=5)
        assert len(hot) == 1
        assert hot[0].title == "今天的大肥鱼"
        assert hot[0].cover == "https://i1.hdslb.com/cover.jpg"
        assert hot[0].url.endswith("BV1xx411c7mD")
        assert hot[0].stats["view"] == 1000

        ranking = await client.ranking(limit=5)
        assert ranking[0].bvid == "BV1xx411c7mD"
    finally:
        await client.aclose()
        await http_client.aclose()


async def test_search_uses_wbi_signature() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/x/web-interface/nav":
            return httpx.Response(
                200,
                json={
                    "code": -101,
                    "message": "账号未登录",
                    "data": {
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB_KEY}.png",
                        }
                    },
                },
            )
        if request.url.path == "/x/web-interface/wbi/search/type":
            params = request.url.params
            assert "buvid3=b3" in request.headers.get("Cookie", "")
            assert params["keyword"] == "大肥鱼"
            assert params["search_type"] == "video"
            assert params["w_rid"]
            assert params["wts"]
            return httpx.Response(200, json={"code": 0, "data": {"result": [SEARCH_ITEM]}})
        raise AssertionError(request.url)

    client, http_client = make_client(handler)
    try:
        videos = await client.search("大肥鱼", limit=3)
        assert [v.title for v in videos] == ["大肥鱼 & 朋友"]
        assert videos[0].author == "UP二号"
        assert videos[0].duration == 754
        assert calls == ["/x/web-interface/nav", "/x/web-interface/wbi/search/type"]
    finally:
        await client.aclose()
        await http_client.aclose()


async def test_search_falls_back_when_risk_controlled() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/x/web-interface/nav":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB_KEY}.png",
                        }
                    },
                },
            )
        if request.url.path == "/x/web-interface/wbi/search/type":
            return httpx.Response(200, json={"code": 0, "data": {"v_voucher": "voucher"}})
        if request.url.path == "/x/web-interface/search/type":
            return httpx.Response(200, json={"code": 0, "data": {"result": [SEARCH_ITEM]}})
        raise AssertionError(request.url)

    client, http_client = make_client(handler)
    try:
        videos = await client.search("大肥鱼", limit=3)
        assert len(videos) == 1
        assert videos[0].title == "大肥鱼 & 朋友"
        assert "/x/web-interface/wbi/search/type" in calls
        assert "/x/web-interface/search/type" in calls
    finally:
        await client.aclose()
        await http_client.aclose()


async def test_watch_with_subtitles() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/x/web-interface/view":
            assert request.url.params["bvid"] == "BV1xx411c7mD"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "bvid": "BV1xx411c7mD",
                        "aid": 111,
                        "cid": 999,
                        "title": "今天的大肥鱼",
                        "desc": "一条鱼的一天",
                        "pic": "http://i1.hdslb.com/cover.jpg",
                        "owner": {"mid": 5, "name": "养鱼人"},
                        "stat": {"view": 1000, "like": 100, "coin": 3},
                        "duration": 754,
                        "pubdate": 1760000000,
                    },
                },
            )
        if path == "/x/tag/archive/tags":
            return httpx.Response(200, json={"code": 0, "data": [{"tag_name": "日常"}]})
        if path == "/x/v2/reply":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "replies": [
                            {
                                "member": {"uname": "路人甲"},
                                "content": {"message": "太可爱了"},
                                "like": 4,
                            }
                        ]
                    },
                },
            )
        if path == "/x/web-interface/nav":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB_KEY}.png",
                        }
                    },
                },
            )
        if path == "/x/player/wbi/v2":
            assert request.url.params["cid"] == "999"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "subtitle": {
                            "subtitles": [{"subtitle_url": "//aisubtitle.hdslb.com/sub.json"}]
                        }
                    },
                },
            )
        if request.url.host == "aisubtitle.hdslb.com":
            return httpx.Response(200, json={"body": [{"content": "第一句"}, {"content": "第二句"}]})
        raise AssertionError(request.url)

    client, http_client = make_client(handler, cookie="SESSDATA=abc")
    try:
        data = await client.watch("https://www.bilibili.com/video/BV1xx411c7mD")
        assert data["video"]["title"] == "今天的大肥鱼"
        assert data["video"]["标签"] == ["日常"]
        assert data["热门评论"][0]["text"] == "太可爱了"
        assert data["字幕节选"] == "第一句 第二句"
    finally:
        await client.aclose()
        await http_client.aclose()


async def test_subtitles_skipped_without_cookie() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/web-interface/view":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"bvid": "BV1xx411c7mD", "aid": 1, "cid": 2, "title": "t", "pages": [{"cid": 2}]},
                },
            )
        if request.url.path == "/x/tag/archive/tags":
            return httpx.Response(200, json={"code": 0, "data": []})
        if request.url.path == "/x/v2/reply":
            return httpx.Response(200, json={"code": 0, "data": {"replies": []}})
        raise AssertionError(request.url)

    client, http_client = make_client(handler)
    try:
        data = await client.watch("BV1xx411c7mD")
        assert "字幕节选" not in data
    finally:
        await client.aclose()
        await http_client.aclose()


async def test_api_error_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/web-interface/view":
            return httpx.Response(200, json={"code": -404, "message": "啥都木有"})
        raise AssertionError(request.url)

    client, http_client = make_client(handler)
    try:
        with pytest.raises(BiliError, match="404"):
            await client.video("BV1xx411c7mD")
    finally:
        await client.aclose()
        await http_client.aclose()


async def test_risk_control_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(412)

    client, http_client = make_client(handler)
    try:
        with pytest.raises(BiliError, match="412"):
            await client.hot()
    finally:
        await client.aclose()
        await http_client.aclose()


async def test_risk_control_retries_with_new_fingerprint() -> None:
    user_agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        user_agents.append(request.headers.get("User-Agent", ""))
        if len(user_agents) == 1:
            return httpx.Response(412)
        return httpx.Response(200, json={"code": 0, "data": {"list": [POPULAR_ITEM]}})

    client, http_client = make_client(handler, risk_retry_delays=(0,))
    try:
        videos = await client.hot()
        assert [video.bvid for video in videos] == ["BV1xx411c7mD"]
        assert len(user_agents) == 2
        assert user_agents[0] != user_agents[1]
    finally:
        await client.aclose()
        await http_client.aclose()


async def test_risk_control_raises_after_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(412)

    client, http_client = make_client(handler, risk_retry_delays=(0, 0))
    try:
        with pytest.raises(BiliRiskControl, match="412"):
            await client.hot()
        assert calls == 3
    finally:
        await client.aclose()
        await http_client.aclose()


async def test_search_falls_back_to_unsigned_on_412() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/x/web-interface/nav":
            return httpx.Response(
                200,
                json={
                    "code": -101,
                    "message": "账号未登录",
                    "data": {
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB_KEY}.png",
                        }
                    },
                },
            )
        if request.url.path == "/x/web-interface/wbi/search/type":
            return httpx.Response(412)
        if request.url.path == "/x/web-interface/search/type":
            return httpx.Response(200, json={"code": 0, "data": {"result": [SEARCH_ITEM]}})
        raise AssertionError(request.url)

    client, http_client = make_client(handler)
    try:
        videos = await client.search("大肥鱼", limit=3)
        assert [video.bvid for video in videos] == ["BV1yy411c7mE"]
        assert calls[-2:] == [
            "/x/web-interface/wbi/search/type",
            "/x/web-interface/search/type",
        ]
    finally:
        await client.aclose()
        await http_client.aclose()


async def test_bad_video_id() -> None:
    client, http_client = make_client(lambda request: httpx.Response(500))
    try:
        with pytest.raises(BiliError, match="没认出"):
            await client.video("不是视频")
    finally:
        await client.aclose()
        await http_client.aclose()


async def test_history_requires_cookie_and_parses_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/web-interface/history/cursor":
            assert request.url.params["ps"] == "10"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "list": [
                            {
                                "title": "Python 异步",
                                "author_name": "UP",
                                "cover": "//i0.hdslb.com/a.jpg",
                                "duration": 600,
                                "progress": 120,
                                "view_at": 1760000000,
                                "history": {"bvid": "BV1xx411c7mD"},
                            },
                            {"title": "没有 bvid"},
                        ]
                    },
                },
            )
        raise AssertionError(request.url)

    client, http_client = make_client(handler)
    try:
        with pytest.raises(BiliError):
            await client.history()
    finally:
        await client.aclose()
        await http_client.aclose()

    client, http_client = make_client(handler, cookie="SESSDATA=abc")
    try:
        items = await client.history(limit=10)
        assert len(items) == 1
        assert items[0].bvid == "BV1xx411c7mD"
        assert items[0].title == "Python 异步"
        assert items[0].progress == 120
        assert items[0].cover.startswith("https://")
    finally:
        await client.aclose()
        await http_client.aclose()


def test_summarize_danmaku_buckets_and_hotspots() -> None:
    xml = (
        '<i><d p="5.0,1,25,16777215">来了</d>'
        '<d p="15.0,1,25,16777215">来了</d>'
        '<d p="70.0,1,25,16777215">这步很关键</d>'
        '<d p="130.0,1,25,16777215">哈哈哈</d></i>'
    )
    summary = summarize_danmaku(xml, duration=180)

    assert summary.total == 4
    assert summary.per_minute == [2, 1, 1]
    assert [item["开始秒"] for item in summary.hot] == [0, 60, 120]
    assert summary.hot[0]["代表弹幕"] == ["来了"]
    assert summary.to_dict()["总数"] == 4


def test_summarize_danmaku_handles_bad_payload() -> None:
    assert summarize_danmaku("not xml").total == 0
    assert summarize_danmaku("").total == 0


async def test_play_url_prefers_mp4() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/web-interface/nav":
            return httpx.Response(
                200,
                json={
                    "code": -101,
                    "data": {
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB_KEY}.png",
                        }
                    },
                },
            )
        if request.url.path == "/x/web-interface/view":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "bvid": "BV1xx411c7mD",
                        "aid": 111,
                        "cid": 999,
                        "title": "t",
                        "desc": "d",
                        "pic": "",
                        "owner": {"mid": 1, "name": "u"},
                        "stat": {},
                        "duration": 600,
                        "pages": [{"cid": 999}],
                    },
                },
            )
        if request.url.path == "/x/tag/archive/tags":
            return httpx.Response(200, json={"code": 0, "data": []})
        if request.url.path == "/x/player/wbi/playurl":
            return httpx.Response(
                200, json={"code": 0, "data": {"durl": [{"url": "https://cdn.example/v.mp4"}]}}
            )
        raise AssertionError(request.url)

    client, http_client = make_client(handler)
    try:
        url = await client.play_url("BV1xx411c7mD")
    finally:
        await client.aclose()
        await http_client.aclose()

    assert url == "https://cdn.example/v.mp4"
