"""两条后台循环：自己刷 B 站、到点主动找对方聊天。"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .agent import Agent, ProactiveMessage
from .bilibili import BilibiliClient, Video
from .config import Config
from .memory import Memory
from .onebot import OneBotClient

log = logging.getLogger(__name__)


class BrowseLoop:
    """像人一样隔一阵子刷一次 B 站，把看到的东西存进记忆。"""

    def __init__(
        self,
        *,
        config: Config,
        bili: BilibiliClient,
        memory: Memory,
        initial_delay: float = 5.0,
    ) -> None:
        self.config = config
        self.bili = bili
        self.memory = memory
        self.initial_delay = initial_delay

    async def run(self) -> None:
        if not self.config.browse_enabled:
            log.info("自动刷 B 站已关闭")
            return
        await asyncio.sleep(self.initial_delay)
        while True:
            try:
                await self.browse_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("刷 B 站失败，等会儿再试")
            delay = self.config.browse_interval_minutes * 60
            delay *= random.uniform(0.8, 1.3)
            await asyncio.sleep(delay)

    async def browse_once(self) -> int:
        videos: list[Video] = []
        try:
            videos.extend(await self.bili.hot(limit=self.config.browse_limit))
        except Exception as exc:
            log.warning("拉热门失败：%s", exc)
        try:
            videos.extend(await self.bili.ranking(limit=10))
        except Exception as exc:
            log.warning("拉排行榜失败：%s", exc)

        seen: set[str] = set()
        fresh = 0
        for video in videos:
            if not video.bvid or video.bvid in seen:
                continue
            seen.add(video.bvid)
            if self.memory.upsert_video(
                bvid=video.bvid,
                title=video.title,
                author=video.author,
                url=video.url,
                cover=video.cover,
                description=video.description,
                duration=video.duration,
                stats=video.stats,
            ):
                fresh += 1

        self.memory.set_state("last_browse_at", time.time())
        self.memory.set_state("last_browse_count", fresh)
        log.info("刷完 B 站：%d 条，其中 %d 条是新的", len(seen), fresh)
        return fresh


class ProactiveScheduler:
    """到点主动发消息。频率带抖动，有安静时段和每日上限。"""

    def __init__(
        self,
        *,
        config: Config,
        agent: Agent,
        bot: OneBotClient,
        memory: Memory,
        initial_delay: float = 30.0,
        clock: Any | None = None,
    ) -> None:
        self.config = config
        self.agent = agent
        self.bot = bot
        self.memory = memory
        self.initial_delay = initial_delay
        self._clock = clock
        try:
            self.tz = ZoneInfo(config.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            self.tz = None

    async def run(self) -> None:
        if not self.config.proactive_enabled:
            log.info("主动聊天已关闭")
            return
        if not self.config.owner_qq:
            log.warning("没有配置 OWNER_QQ，主动聊天不会启动")
            return
        await asyncio.sleep(self.initial_delay)
        while True:
            try:
                await self.maybe_send()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("主动聊天出错")
                self.schedule_next(15)
            await asyncio.sleep(60)

    async def maybe_send(self, *, force: bool = False) -> ProactiveMessage | None:
        now = time.time()
        session_id = self.config.proactive_session_id
        if session_id is None:
            return None

        today = self._today()
        count = self.memory.get_int(f"proactive_count:{today}", 0)
        if not force:
            if self.is_paused() or self.in_quiet_hours():
                return None

            amount = self.memory.get_float("next_proactive_at", 0)
            if amount <= 0:
                self.schedule_next()
                return None
            if now < amount:
                return None

            last_user = max(
                self.memory.get_float(f"last_user_ts:{session_id}", 0),
                self.memory.get_float("last_user_ts", 0),
            )
            if last_user and now - last_user < 10 * 60:
                self.schedule_next()
                return None

            if count >= self.config.proactive_daily_limit:
                self.schedule_next(60)
                return None

        kind = "browse" if self.memory.unshared_videos(1) else "chat"
        try:
            message = await self.agent.compose_proactive(session_id, kind)
        except Exception as exc:
            log.warning("主动消息生成失败：%s", exc)
            if not force:
                self.schedule_next(15)
            return None

        if not message:
            if not force:
                self.schedule_next()
            return None

        if self.config.share_cover and message.cover:
            try:
                await self.bot.send_image_private(self.config.owner_qq or 0, message.cover)
            except Exception as exc:
                log.debug("封面发送失败：%s", exc)
        await self.bot.send_private(self.config.owner_qq or 0, message.text)

        self.agent.record_outgoing(session_id, message.text)
        if message.bvid:
            self.memory.mark_video_shared(message.bvid)
        self.memory.set_state(f"proactive_count:{today}", count + 1)
        self.memory.set_state("last_proactive_at", now)
        self.schedule_next()
        log.info("主动发出（%s）：%s", kind, message.text[:60])
        return message

    def postpone(self) -> None:
        if self.config.proactive_enabled:
            self.schedule_next()

    def schedule_next(self, minutes: float | None = None) -> None:
        delay = self._random_delay() if minutes is None else max(minutes, 1) * 60
        self.memory.set_state("next_proactive_at", time.time() + delay)

    def is_paused(self) -> bool:
        return self.memory.get_float("proactive_paused_until", 0) > time.time()

    def in_quiet_hours(self, now: datetime | None = None) -> bool:
        quiet = self.config.quiet_hours
        if not quiet:
            return False
        current = now or (datetime.now(self.tz) if self.tz else datetime.now())
        start, end = quiet
        hour = current.hour
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def _random_delay(self) -> float:
        return random.uniform(self.config.proactive_min_minutes, self.config.proactive_max_minutes) * 60

    def _today(self) -> str:
        now = self._clock() if self._clock else (datetime.now(self.tz) if self.tz else datetime.now())
        return now.strftime("%Y-%m-%d")
