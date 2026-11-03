"""OneBot v11（NapCat / LLOneBot）客户端：连 WebSocket、收消息、发消息。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

try:
    from websockets.asyncio.client import connect
except ImportError:  # pragma: no cover - 兼容 websockets < 13
    from websockets.client import connect  # type: ignore[no-redef]

log = logging.getLogger(__name__)

CQ_RE = re.compile(r"\[CQ:([a-zA-Z_]+)((?:,[^\]]*)?)\]")
FACE_RE = re.compile(r"\[\[?face:(\d{1,3})\]\]?")


class OneBotError(RuntimeError):
    pass


@dataclass(slots=True)
class IncomingMessage:
    message_type: str
    user_id: int
    self_id: int
    group_id: int | None = None
    message_id: Any = None
    sender_name: str = ""
    text: str = ""
    segments: list[dict[str, Any]] = field(default_factory=list)
    at_me: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_group(self) -> bool:
        return self.message_type == "group" and self.group_id is not None

    @property
    def session_id(self) -> str:
        if self.is_group:
            return f"group:{self.group_id}:{self.user_id}"
        return f"private:{self.user_id}"


def normalize_segments(message: Any) -> list[dict[str, Any]]:
    if isinstance(message, list):
        segments = []
        for segment in message:
            if not isinstance(segment, dict):
                continue
            data = segment.get("data")
            segments.append({"type": segment.get("type", ""), "data": data if isinstance(data, dict) else {}})
        return segments
    if isinstance(message, str):
        return _parse_cq_string(message)
    return []


def _parse_cq_string(message: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    position = 0
    for match in CQ_RE.finditer(message):
        if match.start() > position:
            segments.append({"type": "text", "data": {"text": message[position : match.start()]}})
        data: dict[str, str] = {}
        for chunk in match.group(2).lstrip(",").split(","):
            if "=" in chunk:
                key, value = chunk.split("=", 1)
                data[key.strip()] = value.strip()
        segments.append({"type": match.group(1), "data": data})
        position = match.end()
    if position < len(message):
        segments.append({"type": "text", "data": {"text": message[position:]}})
    return segments


def segments_to_text(segments: list[dict[str, Any]]) -> str:
    parts = []
    for segment in segments:
        if segment.get("type") == "text":
            parts.append(str((segment.get("data") or {}).get("text") or ""))
    return "".join(parts).strip()


def parse_message_event(event: dict[str, Any], self_id: int) -> IncomingMessage | None:
    if event.get("post_type") != "message":
        return None
    message_type = event.get("message_type")
    if message_type not in ("private", "group"):
        return None

    user_id = int(event.get("user_id") or 0)
    if not user_id or user_id == self_id:
        return None

    segments = normalize_segments(event.get("message", event.get("raw_message", "")))
    text = segments_to_text(segments)
    at_me = any(
        segment.get("type") == "at" and str((segment.get("data") or {}).get("qq") or "") == str(self_id)
        for segment in segments
    )
    if not at_me and message_type == "group":
        at_me = f"[CQ:at,qq={self_id}]" in str(event.get("raw_message") or "")

    sender = event.get("sender") or {}
    sender_name = sender.get("card") or sender.get("nickname") or str(user_id)

    group_id = event.get("group_id")
    return IncomingMessage(
        message_type=message_type,
        user_id=user_id,
        self_id=self_id,
        group_id=int(group_id) if group_id else None,
        message_id=event.get("message_id"),
        sender_name=str(sender_name),
        text=text,
        segments=segments,
        at_me=at_me,
        raw=event,
    )


def text_to_segments(text: str) -> list[dict[str, Any]]:
    """把回复里的 [[face:14]] 标记拆成 OneBot 的 face 段。"""
    value = text or ""
    segments: list[dict[str, Any]] = []
    position = 0
    for match in FACE_RE.finditer(value):
        if match.start() > position:
            chunk = value[position : match.start()]
            if chunk:
                segments.append({"type": "text", "data": {"text": chunk}})
        segments.append({"type": "face", "data": {"id": match.group(1)}})
        position = match.end()
    if position < len(value):
        segments.append({"type": "text", "data": {"text": value[position:]}})
    if not segments:
        segments.append({"type": "text", "data": {"text": value}})
    return segments


class OneBotClient:
    def __init__(
        self,
        *,
        url: str,
        token: str = "",
        on_message: Callable[[IncomingMessage], Awaitable[None] | None] | None = None,
        on_request: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        self.url = url
        self.token = token
        self.on_message = on_message
        self.on_request = on_request
        self.self_id: int = 0
        self._ws: Any = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._connected = asyncio.Event()
        self._stop = asyncio.Event()
        self._backoff = 1.0

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def wait_connected(self, timeout: float = 20.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def run(self) -> None:
        log.info("连接 OneBot：%s", self.url)
        while not self._stop.is_set():
            try:
                await self._connect()
                self._backoff = 1.0
                async for raw in self._ws:
                    self._handle_raw(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._stop.is_set():
                    log.warning("OneBot 连接断开：%s（%.0f 秒后重连）", exc, self._backoff)
            finally:
                self._connected.clear()
                self._fail_pending("连接已断开")
                await self._close_ws()

            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(self._stop.wait(), self._backoff)
            except asyncio.TimeoutError:
                pass
            self._backoff = min(self._backoff * 2, 30.0)

    async def _connect(self) -> None:
        headers = {
            "User-Agent": "Dafeiyu/1.0 (OneBot v11)",
            "X-Client-Role": "Universal",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        options = {"ping_interval": 20, "ping_timeout": 20, "max_size": 32 * 1024 * 1024}
        try:
            self._ws = await connect(self.url, additional_headers=headers, **options)
        except TypeError:  # 老版本 websockets 的参数名
            self._ws = await connect(self.url, extra_headers=headers, **options)
        self._connected.set()
        log.info("OneBot 已连接")

    async def stop(self) -> None:
        self._stop.set()
        await self._close_ws()

    async def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _handle_raw(self, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            log.debug("收到非 JSON 消息：%r", raw)
            return
        if not isinstance(data, dict):
            return

        echo = data.get("echo")
        if echo is not None:
            future = self._pending.pop(str(echo), None)
            if future and not future.done():
                future.set_result(data)
            return

        if data.get("self_id"):
            self.self_id = int(data["self_id"])

        if data.get("post_type") == "message":
            message = parse_message_event(data, self.self_id)
            if message is None:
                return
            if self.on_message is None:
                return
            task = asyncio.create_task(self._dispatch(message))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif data.get("post_type") == "request" and self.on_request is not None:
            task = asyncio.create_task(self._dispatch_request(data))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _dispatch(self, message: IncomingMessage) -> None:
        try:
            result = self.on_message(message) if self.on_message else None
            if asyncio.iscoroutine(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("处理消息失败：%s", message.text[:80])

    async def _dispatch_request(self, event: dict[str, Any]) -> None:
        try:
            result = self.on_request(event) if self.on_request else None
            if asyncio.iscoroutine(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("处理请求事件失败")

    async def call(self, action: str, params: dict[str, Any] | None = None, *, timeout: float = 20.0) -> dict[str, Any]:
        if not self.connected or self._ws is None:
            try:
                await asyncio.wait_for(self._connected.wait(), timeout=min(timeout, 5.0))
            except asyncio.TimeoutError:
                raise OneBotError("还没连上 OneBot，检查 NapCat 的 WebSocket 服务有没有开") from None

        echo = uuid.uuid4().hex
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        payload = {"action": action, "params": params or {}, "echo": echo}
        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
            response = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise OneBotError(f"OneBot 调用超时：{action}") from None
        except Exception as exc:
            raise OneBotError(f"OneBot 调用失败：{exc}") from exc
        finally:
            self._pending.pop(echo, None)

        retcode = response.get("retcode")
        if response.get("status") == "failed" or (retcode not in (0, None)):
            raise OneBotError(f"OneBot 返回错误（{retcode}）：{response.get('message') or response.get('wording') or response}")
        return response

    async def send_private(self, user_id: int, text: str) -> dict[str, Any]:
        return await self._send("private", user_id, text)

    async def send_group(self, group_id: int, text: str, *, at_user: int | None = None) -> dict[str, Any]:
        return await self._send("group", group_id, text, at_user=at_user)

    async def send_image_private(self, user_id: int, url: str) -> dict[str, Any]:
        message = [{"type": "image", "data": {"file": url}}]
        return await self.call("send_private_msg", {"user_id": user_id, "message": message})

    async def send_image_group(self, group_id: int, url: str) -> dict[str, Any]:
        message = [{"type": "image", "data": {"file": url}}]
        return await self.call("send_group_msg", {"group_id": group_id, "message": message})

    async def _send(self, kind: str, target: int, text: str, *, at_user: int | None = None) -> dict[str, Any]:
        message: list[dict[str, Any]] = []
        if kind == "group" and at_user:
            message.append({"type": "at", "data": {"qq": str(at_user)}})
            message.append({"type": "text", "data": {"text": " "}})
        message.extend(text_to_segments(text))
        if kind == "group":
            return await self.call("send_group_msg", {"group_id": target, "message": message})
        return await self.call("send_private_msg", {"user_id": target, "message": message})

    def _fail_pending(self, reason: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(OneBotError(reason))
        self._pending.clear()

    async def aclose(self) -> None:
        await self.stop()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
