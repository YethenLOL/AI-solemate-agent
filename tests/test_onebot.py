from __future__ import annotations

import asyncio
import json
import socket

import pytest

from dafeiyu.onebot import (
    IncomingMessage,
    OneBotClient,
    OneBotError,
    parse_message_event,
    segments_to_text,
)

try:
    from websockets.asyncio.server import serve
except ImportError:  # pragma: no cover
    from websockets.server import serve


SELF_ID = 10000


def private_event(text: str = "你好") -> dict:
    return {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "self_id": SELF_ID,
        "user_id": 123,
        "message_id": 1,
        "sender": {"nickname": "小明"},
        "message": [{"type": "text", "data": {"text": text}}],
    }


def test_parse_private_message() -> None:
    message = parse_message_event(private_event(), SELF_ID)
    assert message is not None
    assert message.text == "你好"
    assert message.session_id == "private:123"
    assert message.sender_name == "小明"
    assert message.at_me is False


def test_parse_group_at_message() -> None:
    event = {
        "post_type": "message",
        "message_type": "group",
        "self_id": SELF_ID,
        "user_id": 123,
        "group_id": 999,
        "message_id": 2,
        "sender": {"card": "群名片"},
        "message": [
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "text", "data": {"text": " 看看热门"}},
        ],
    }
    message = parse_message_event(event, SELF_ID)
    assert message is not None
    assert message.is_group
    assert message.at_me
    assert message.text == "看看热门"
    assert message.session_id == "group:999:123"
    assert message.sender_name == "群名片"


def test_parse_cq_string() -> None:
    event = {
        "post_type": "message",
        "message_type": "group",
        "self_id": SELF_ID,
        "user_id": 123,
        "group_id": 999,
        "message": f"[CQ:at,qq={SELF_ID}] 在吗",
        "raw_message": f"[CQ:at,qq={SELF_ID}] 在吗",
        "sender": {"nickname": "小明"},
    }
    message = parse_message_event(event, SELF_ID)
    assert message is not None
    assert message.at_me is True
    assert message.text == "在吗"


def test_ignore_self_and_non_message() -> None:
    own = private_event()
    own["user_id"] = SELF_ID
    assert parse_message_event(own, SELF_ID) is None

    notice = {"post_type": "notice", "notice_type": "group_recall"}
    assert parse_message_event(notice, SELF_ID) is None


def test_segments_to_text() -> None:
    segments = [
        {"type": "at", "data": {"qq": "1"}},
        {"type": "text", "data": {"text": " 你好"}},
        {"type": "image", "data": {"file": "x.jpg"}},
    ]
    assert segments_to_text(segments) == "你好"


def test_incoming_session_id() -> None:
    message = IncomingMessage(message_type="group", user_id=1, self_id=2, group_id=3)
    assert message.session_id == "group:3:1"
    private = IncomingMessage(message_type="private", user_id=1, self_id=2)
    assert private.session_id == "private:1"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_websocket_call_and_event() -> None:
    port = _free_port()
    auth_header: dict[str, str] = {}
    replied = asyncio.Event()

    async def handler(ws) -> None:
        request = getattr(ws, "request", None)
        headers = request.headers if request is not None else getattr(ws, "request_headers", {})
        auth_header["value"] = headers.get("Authorization", "")
        async for raw in ws:
            data = json.loads(raw)
            await ws.send(
                json.dumps(
                    {"status": "ok", "retcode": 0, "data": {"message_id": 7}, "echo": data["echo"]}
                )
            )
            await ws.send(json.dumps(private_event("你好呀")))
            await ws.send(
                json.dumps(
                    {
                        "post_type": "request",
                        "request_type": "friend",
                        "self_id": SELF_ID,
                        "user_id": 123,
                        "flag": "flag-1",
                    }
                )
            )
            replied.set()

    server = await serve(handler, "127.0.0.1", port)
    received: list[IncomingMessage] = []
    event = asyncio.Event()
    request_event = asyncio.Event()

    requests: list[dict] = []

    def on_message(message: IncomingMessage) -> None:
        received.append(message)
        event.set()

    def on_request(request: dict) -> None:
        requests.append(request)
        request_event.set()

    client = OneBotClient(
        url=f"ws://127.0.0.1:{port}",
        token="secret",
        on_message=on_message,
        on_request=on_request,
    )
    task = asyncio.create_task(client.run())
    try:
        await client.wait_connected(timeout=5)
        response = await client.send_private(123, "hi")
        assert response["data"]["message_id"] == 7
        await asyncio.wait_for(event.wait(), timeout=5)
        assert auth_header["value"] == "Bearer secret"
        assert received[0].text == "你好呀"
        await asyncio.wait_for(request_event.wait(), timeout=5)
        assert requests[0]["flag"] == "flag-1"
    finally:
        await client.aclose()
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        server.close()
        await server.wait_closed()


async def test_call_before_connect_raises() -> None:
    client = OneBotClient(url="ws://127.0.0.1:1")
    with pytest.raises(OneBotError, match="还没连上"):
        await client.call("send_private_msg", {"user_id": 1, "message": []}, timeout=0.1)


def test_text_to_segments_converts_face_markers() -> None:
    from dafeiyu.onebot import text_to_segments

    segments = text_to_segments("开心 [[face:14]] 晚安 [[face:75]]")
    assert [segment["type"] for segment in segments] == ["text", "face", "text", "face"]
    assert segments[1]["data"]["id"] == "14"
    assert segments[3]["data"]["id"] == "75"
