"""定时记录「主人电脑上正在用什么」，供私聊上下文和 recent_activity 工具使用。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .computer import ComputerController, ComputerError
from .config import Config
from .memory import Memory

log = logging.getLogger(__name__)

PRIVATE_TITLE_WORDS = (
    "无痕",
    "隐私浏览",
    "incognito",
    "private browsing",
    "1password",
    "keychain",
    "密码",
)


class ActivityMonitor:
    def __init__(
        self,
        *,
        config: Config,
        memory: Memory,
        computer: ComputerController,
        initial_delay: float = 10.0,
    ) -> None:
        self.config = config
        self.memory = memory
        self.computer = computer
        self.initial_delay = initial_delay
        self._last: tuple[str, str] | None = None
        self._last_prune = 0.0

    async def run(self) -> None:
        if not self.config.activity_enabled:
            log.info("电脑活动感知已关闭")
            return
        if not self.computer.is_macos:
            log.info("非 macOS，电脑活动感知不会启动")
            return
        await asyncio.sleep(self.initial_delay)
        while True:
            try:
                await self.sample()
            except asyncio.CancelledError:
                raise
            except ComputerError as exc:
                log.warning("读取电脑活动失败：%s", exc)
            except Exception:
                log.exception("电脑活动感知出错")
            await asyncio.sleep(self.config.activity_interval_seconds)

    async def sample(self) -> dict[str, Any] | None:
        status = await self.computer.get_status()
        app = str(status.get("app") or "").strip()
        title = str(status.get("window") or "").strip()
        idle = int(status.get("idle_seconds") or 0)
        if not app:
            return None
        if idle > self.config.activity_idle_skip_seconds:
            return None
        if self._blocked(app, title):
            return None
        key = (app, title)
        if key == self._last:
            return None
        self._last = key
        self.memory.add_activity(app, title, idle)
        now = time.time()
        if now - self._last_prune > 86400:
            self.memory.prune_activity(self.config.activity_retention_days)
            self._last_prune = now
        log.debug("记录电脑活动：%s | %s", app, title[:80])
        return {"app": app, "title": title, "idle_seconds": idle}

    def _blocked(self, app: str, title: str) -> bool:
        app_lower = app.lower()
        title_lower = title.lower()
        for item in self.config.activity_blocklist:
            item = item.strip().lower()
            if item and item in app_lower:
                return True
        if app_lower in ("loginwindow", "screensaver", "screensharingd"):
            return True
        return any(word in title_lower for word in PRIVATE_TITLE_WORDS)
