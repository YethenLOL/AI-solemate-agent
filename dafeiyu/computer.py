"""在 macOS 上执行少量、可审计的安全操作（不含任意 shell）。"""

from __future__ import annotations

import asyncio
import platform
import re
from typing import Any
from urllib.parse import quote_plus, urlparse

OSASCRIPT = "/usr/bin/osascript"
OPEN = "/usr/bin/open"
IOREG = "/usr/sbin/ioreg"


class ComputerError(RuntimeError):
    pass


class ComputerController:
    def __init__(
        self,
        *,
        enabled: bool = True,
        allowed_apps: tuple[str, ...] = (),
        search_url: str = "https://www.baidu.com/s?wd=",
        timeout: float = 10.0,
    ) -> None:
        self.enabled = enabled
        self.allowed_apps = tuple(app.strip() for app in allowed_apps if app.strip())
        self.search_url = search_url
        self.timeout = timeout
        self.is_macos = platform.system() == "Darwin"

    async def _run(self, *args: str) -> str:
        if not self.is_macos:
            raise ComputerError("电脑操作目前只支持 macOS")
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ComputerError(f"启动命令失败：{exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise ComputerError("操作超时") from None
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            raise ComputerError((err or out or f"命令返回 {process.returncode}")[:300])
        return out

    async def execute(self, action: str, **params: Any) -> dict[str, Any]:
        if not self.enabled:
            raise ComputerError("电脑操作已关闭（COMPUTER_ENABLED=false）")
        handlers = {
            "status": self.get_status,
            "open_app": self.open_app,
            "open_url": self.open_url,
            "search_web": self.search_web,
            "set_volume": self.set_volume,
            "notify": self.notify,
            "media": self.media,
            "lock_screen": self.lock_screen,
        }
        handler = handlers.get(action)
        if handler is None:
            raise ComputerError(f"不支持的动作：{action}")
        return await handler(**params)

    async def get_status(self) -> dict[str, Any]:
        script = '''tell application "System Events"
set frontApp to first application process whose frontmost is true
set appName to name of frontApp
set winTitle to ""
try
  set winTitle to name of front window of frontApp
end try
return appName & linefeed & winTitle
end tell'''
        output = await self._run(OSASCRIPT, "-e", script)
        lines = output.splitlines()
        app = lines[0].strip() if lines else ""
        title = lines[1].strip() if len(lines) > 1 else ""
        idle = await self._idle_seconds()
        return {
            "ok": True,
            "action": "status",
            "app": app,
            "window": title[:200],
            "idle_seconds": idle,
        }

    async def _idle_seconds(self) -> int:
        try:
            output = await self._run(IOREG, "-c", "IOHIDSystem")
        except ComputerError:
            return 0
        match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', output)
        if not match:
            return 0
        return int(match.group(1)) // 1_000_000_000

    async def open_app(self, app: str = "", **_: Any) -> dict[str, Any]:
        name = str(app or "").strip()
        if not name or len(name) > 100 or name.startswith("-") or any(ch in name for ch in "\r\n\x00"):
            raise ComputerError("App 名称不合法")
        if self.allowed_apps and name not in self.allowed_apps:
            raise ComputerError(f"这个 App 不在允许列表：{name}")
        await self._run(OPEN, "-a", name)
        return {"ok": True, "action": "open_app", "app": name}

    async def open_url(self, url: str = "", **_: Any) -> dict[str, Any]:
        target = str(url or "").strip()
        if len(target) > 2000:
            raise ComputerError("链接太长")
        parsed = urlparse(target)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ComputerError("只允许打开 http/https 链接")
        await self._run(OPEN, target)
        return {"ok": True, "action": "open_url", "url": target}

    async def search_web(self, query: str = "", **_: Any) -> dict[str, Any]:
        keyword = str(query or "").strip()
        if not keyword:
            raise ComputerError("搜索词不能为空")
        if len(keyword) > 200:
            raise ComputerError("搜索词太长")
        url = self.search_url + quote_plus(keyword)
        await self._run(OPEN, url)
        return {"ok": True, "action": "search_web", "query": keyword, "url": url}

    async def set_volume(self, level: Any = 50, **_: Any) -> dict[str, Any]:
        try:
            volume = int(level)
        except (TypeError, ValueError):
            raise ComputerError("音量需要是 0~100 的整数") from None
        volume = max(0, min(100, volume))
        script = "on run argv\nset volume output volume (item 1 of argv as integer)\nend run"
        await self._run(OSASCRIPT, "-e", script, str(volume))
        return {"ok": True, "action": "set_volume", "level": volume}

    async def notify(self, title: str = "大肥鱼", message: str = "", **_: Any) -> dict[str, Any]:
        heading = str(title or "大肥鱼").strip()[:100]
        body = str(message or "").strip()[:300]
        if not body:
            raise ComputerError("通知内容不能为空")
        script = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"
        await self._run(OSASCRIPT, "-e", script, heading, body)
        return {"ok": True, "action": "notify", "title": heading}

    async def media(self, media_action: str = "play_pause", **_: Any) -> dict[str, Any]:
        commands = {
            "play_pause": "playpause",
            "next": "next track",
            "previous": "previous track",
            "stop": "stop",
        }
        command = commands.get(str(media_action))
        if command is None:
            raise ComputerError("media_action 只能是 play_pause / next / previous / stop")
        await self._run(OSASCRIPT, "-e", f'tell application "Music" to {command}')
        return {"ok": True, "action": "media", "media_action": media_action}

    async def lock_screen(self, **_: Any) -> dict[str, Any]:
        script = 'tell application "System Events" to keystroke "q" using {control down, command down}'
        await self._run(OSASCRIPT, "-e", script)
        return {"ok": True, "action": "lock_screen"}
