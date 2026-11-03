"""消息路由：白名单、群聊唤醒、指令、发送回复。"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .agent import Agent
from .config import Config
from .llm import LLMError
from .memory import Memory
from .onebot import IncomingMessage, OneBotClient

log = logging.getLogger(__name__)

HELP_TEXT = (
    "大肥鱼会这些：\n"
    "· 闲聊、记事：说「记住 xxx」我就记下来\n"
    "· 刷 B 站：让我「看看热门」「搜一下 xxx」「看下 BVxxxx」\n"
    "· /状态 看看我在干嘛；/人格 看当前人格卡\n"
    "· /在干嘛 看你现在用的 App 和窗口（只有主人能用）\n"
    "· /刷一下 立刻去 B 站刷一轮；/主动 立刻主动说一句\n"
    "· /安静 2 小时别主动找你；/醒醒 恢复\n"
    "· /忘掉对话 清空当前聊天记录"
)


class Router:
    def __init__(
        self,
        *,
        config: Config,
        agent: Agent,
        bot: OneBotClient,
        memory: Memory,
        scheduler: Any | None = None,
        browse: Any | None = None,
    ) -> None:
        self.config = config
        self.agent = agent
        self.bot = bot
        self.memory = memory
        self.scheduler = scheduler
        self.browse = browse
        self._locks: dict[str, asyncio.Lock] = {}
        self._semaphore = asyncio.Semaphore(3)
        try:
            self._tz: ZoneInfo | None = ZoneInfo(config.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            self._tz = None

    async def handle_request(self, event: dict[str, Any]) -> None:
        """只自动通过主人/白名单号发来的好友请求。"""
        if event.get("request_type") != "friend":
            return
        if not self.config.auto_accept_friends:
            return
        user_id = int(event.get("user_id") or 0)
        if not (self._is_owner(user_id) or user_id in self.config.allowed_users):
            log.info("忽略好友请求：%s", user_id)
            return
        flag = event.get("flag")
        if not flag:
            return
        try:
            await self.bot.call("set_friend_add_request", {"flag": flag, "approve": True})
            log.info("已通过好友请求：%s", user_id)
        except Exception:
            log.exception("通过好友请求失败")

    async def handle(self, message: IncomingMessage) -> None:
        if not self._is_allowed(message):
            log.info("忽略未授权消息：%s %s", message.user_id, message.text[:40])
            return

        if self._is_owner(message.user_id) and not message.is_group:
            self.memory.set_state("last_owner_message_at", time.time())
        self._record_stickers(message)
        text = self._strip_wake_word(message)
        if not text:
            return

        if await self._handle_command(message, text):
            return

        if self.scheduler is not None:
            self.scheduler.postpone()

        lock = self._locks.setdefault(message.session_id, asyncio.Lock())
        try:
            async with lock, self._semaphore:
                reply = await self.agent.respond(
                    message.session_id,
                    text,
                    in_group=message.is_group,
                )
        except LLMError as exc:
            log.warning("模型调用失败：%s", exc)
            reply = "咕噜…鱼脑子卡住了（模型那边没连上），等我缓过来再聊。"
        except Exception:
            log.exception("生成回复失败")
            reply = "咕噜咕噜…鱼刚才呛水了，你再发一次试试。"

        if reply:
            await self._send(message, reply)

    def _record_stickers(self, message: IncomingMessage) -> None:
        for segment in message.segments:
            data = segment.get("data") or {}
            if segment.get("type") == "face":
                sticker_id = str(data.get("id") or "").strip()
                if sticker_id.isdigit():
                    self.memory.record_sticker("face", sticker_id)
            elif segment.get("type") == "mface":
                sticker_id = str(data.get("emoji_id") or data.get("summary") or "").strip()
                if sticker_id:
                    self.memory.record_sticker("mface", sticker_id[:80])

    # ------------------------------------------------------------------ 过滤
    def _is_allowed(self, message: IncomingMessage) -> bool:
        if message.is_group:
            if not self.config.allows_group(message.group_id or 0):
                return False
            if not self.config.group_at_required:
                return True
            if message.at_me:
                return True
            return self._has_wake_word(message.text)
        return self.config.allows_private(message.user_id)

    def _has_wake_word(self, text: str) -> bool:
        stripped = text.lstrip()
        return any(word and stripped.startswith(word) for word in self.config.wake_words)

    def _strip_wake_word(self, message: IncomingMessage) -> str:
        text = message.text.strip()
        if message.at_me:
            text = text.lstrip(" ，,：:")
        for word in self.config.wake_words:
            if word and text.startswith(word):
                text = text[len(word) :].lstrip(" ，,：:，。！？!?")
                break
        return text.strip()

    # ------------------------------------------------------------------ 指令
    async def _handle_command(self, message: IncomingMessage, text: str) -> bool:
        lowered = text.lower()
        if lowered in ("/help", "/帮助", "help"):
            await self._send(message, HELP_TEXT)
            return True

        if lowered in ("/status", "/状态"):
            await self._send(message, self._status_text())
            return True

        if lowered in ("/在干嘛", "/在做什么", "/电脑"):
            if not self._is_owner(message.user_id):
                await self._send(message, "这条指令只有主人能下。")
                return True
            if not self.config.computer_enabled:
                await self._send(message, "电脑操作已关闭。")
                return True
            try:
                status = await self.agent.computer.get_status()
            except Exception as exc:
                log.warning("读取电脑状态失败：%s", exc)
                await self._send(message, "电脑那边没读出来，可能是辅助功能权限没给。")
                return True
            app = status.get("app") or "未知 App"
            title = status.get("window") or ""
            idle = int(status.get("idle_seconds") or 0)
            idle_text = f"{idle // 60} 分钟" if idle >= 60 else f"{idle} 秒"
            text = f"你现在在用 {app}"
            if title:
                text += f"，窗口是「{title}」"
            text += f"，已空闲 {idle_text}。"
            await self._send(message, text)
            return True

        if lowered in ("/人格", "/persona"):
            if not self._is_owner(message.user_id):
                await self._send(message, "这条指令只有主人能下。")
                return True
            card = getattr(self.agent, "persona_card", None)
            if card is None:
                await self._send(message, "当前没有加载人格卡。")
                return True
            text = card.load()
            await self._send(
                message,
                f"人格卡：{card.path}\n{len(text)} 字符 · {'文件' if card.source == 'file' else '内置'}\n"
                "改文件并保存后，下一条消息自动生效。",
            )
            return True

        if lowered in ("/刷一下", "/刷b站"):
            if not self._is_owner(message.user_id):
                await self._send(message, "这条指令只有主人能下。")
                return True
            if self.browse is None:
                await self._send(message, "现在没有在自动刷 B 站。")
                return True
            try:
                fresh = await self.browse.browse_once()
            except Exception as exc:
                log.warning("手动刷 B 站失败：%s", exc)
                await self._send(message, "B 站那边没刷动，等会儿再试。")
                return True
            await self._send(message, f"刷完了，看到 {fresh} 条新视频。")
            return True

        if lowered in ("/主动", "/说一句"):
            if not self._is_owner(message.user_id):
                await self._send(message, "这条指令只有主人能下。")
                return True
            if self.scheduler is None:
                await self._send(message, "主动模式没开。")
                return True
            try:
                sent = await self.scheduler.maybe_send(force=True)
            except Exception as exc:
                log.warning("手动主动消息失败：%s", exc)
                sent = None
            if sent is None:
                await self._send(message, "刚憋了半天，没憋出想说的话，再试一次？")
            else:
                await self._send(message, "已经主动说了一句，去私聊看看。")
            return True

        if lowered.startswith("/安静") or lowered.startswith("/闭嘴"):
            if not self._is_owner(message.user_id):
                await self._send(message, "这条指令只有主人能下，鱼不敢听别人的。")
                return True
            minutes = self._parse_minutes(text, default=120)
            until = time.time() + minutes * 60
            self.memory.set_state("proactive_paused_until", until)
            await self._send(message, f"好，接下来 {minutes} 分钟不主动烦你。咕噜。")
            return True

        if lowered in ("/醒醒", "/开机"):
            if not self._is_owner(message.user_id):
                await self._send(message, "这条指令只有主人能下。")
                return True
            self.memory.delete_state("proactive_paused_until")
            if self.scheduler is not None:
                self.scheduler.postpone()
            await self._send(message, "醒了醒了，尾巴都甩起来了。")
            return True

        if lowered in ("/忘掉对话", "/忘掉"):
            if not self._is_owner(message.user_id):
                await self._send(message, "这条指令只有主人能下。")
                return True
            deleted = self.memory.clear_session(message.session_id)
            await self._send(message, f"这页翻过去了（清了 {deleted} 条）。")
            return True

        return False

    def _is_owner(self, user_id: int) -> bool:
        return bool(self.config.owner_qq and user_id == self.config.owner_qq)

    @staticmethod
    def _parse_minutes(text: str, *, default: int) -> int:
        parts = text.replace("：", " ").split()
        for part in parts[1:]:
            if part.isdigit():
                return max(1, min(int(part), 24 * 60))
        return default

    def _status_text(self) -> str:
        now = datetime.now(self._tz) if self._tz else datetime.now()
        paused_until = self.memory.get_float("proactive_paused_until", 0)
        if paused_until > time.time():
            pause = f"暂停主动到 {datetime.fromtimestamp(paused_until, self._tz).strftime('%H:%M')}"
        else:
            pause = "会主动找你"
        last = self.memory.get_float("last_proactive_at", 0)
        last_text = datetime.fromtimestamp(last, self._tz).strftime("%m-%d %H:%M") if last else "还没主动说过话"
        browsed = self.memory.get_float("last_browse_at", 0)
        browse_text = datetime.fromtimestamp(browsed, self._tz).strftime("%m-%d %H:%M") if browsed else "还没刷过"
        return (
            f"现在 {now.strftime('%m-%d %H:%M')}\n"
            f"模型：{self.config.llm_model}\n"
            f"记得 {self.memory.count_facts()} 件事，刷过 {self.memory.count_videos()} 个视频（上次 {browse_text}）\n"
            f"主动模式：{pause}（上次：{last_text}）"
        )

    # ------------------------------------------------------------------ 发送
    async def _send(self, message: IncomingMessage, text: str) -> None:
        chunks = split_message(text, limit=1000)
        for chunk in chunks:
            if message.is_group:
                await self.bot.send_group(
                    message.group_id or 0,
                    chunk,
                    at_user=message.user_id if self.config.group_reply_at else None,
                )
            else:
                await self.bot.send_private(message.user_id, chunk)


def split_message(text: str, limit: int = 1000) -> list[str]:
    """按段落切分，避免一段话太长被 QQ 吞掉。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current.rstrip())
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.rstrip())
    return [chunk for chunk in chunks if chunk.strip()]
