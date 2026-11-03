"""B 站请求保护：读/写预算 + 最小间隔 + 412 递增暂停。

状态落在 APP_HOME/data/traffic.json，用 flock 做跨进程互斥，
所以 GUI、CLI、后台学习共用同一份额度。
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

PAUSE_MINUTES = (5, 10, 20, 30)


class TrafficPaused(RuntimeError):
    """请求被本地保护层拦下（预算耗尽 / 冷却中 / 平台风控暂停）。"""


@dataclass(slots=True)
class TrafficSettings:
    min_gap_ms: int = 600
    read_hourly: int = 600
    read_daily: int = 3000
    write_hourly: int = 12
    write_daily: int = 30
    write_gap_seconds: int = 120

    def normalized(self) -> "TrafficSettings":
        return TrafficSettings(
            min_gap_ms=max(200, int(self.min_gap_ms)),
            read_hourly=max(1, int(self.read_hourly)),
            read_daily=max(1, int(self.read_daily)),
            write_hourly=max(1, int(self.write_hourly)),
            write_daily=max(1, int(self.write_daily)),
            write_gap_seconds=max(0, int(self.write_gap_seconds)),
        )


def _empty_state(day: str) -> dict[str, Any]:
    return {
        "date": day,
        "hour_at": 0.0,
        "read_hour": 0,
        "read_day": 0,
        "write_hour": 0,
        "write_day": 0,
        "last_at": 0.0,
        "last_write": 0.0,
        "until": 0.0,
        "reason": "",
        "strikes": 0,
        "last_412": 0.0,
    }


class TrafficGuard:
    def __init__(
        self,
        path: str | Path | None = None,
        settings: TrafficSettings | None = None,
        *,
        clock: Callable[[], float] = time.time,
        enabled: bool = True,
    ) -> None:
        self.path = Path(path).expanduser() if path else None
        self.settings = (settings or TrafficSettings()).normalized()
        self.clock = clock
        self.enabled = enabled
        self._async_lock = asyncio.Lock()
        self._thread_lock = threading.Lock()

    # ------------------------------------------------------------------ 状态
    def _load(self) -> dict[str, Any]:
        if self.path is None:
            return _empty_state(date.today().isoformat())
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            raise TrafficPaused(f"请求保护状态损坏：{exc}；请检查 {self.path}") from exc
        return _empty_state(date.today().isoformat())

    def _save(self, state: dict[str, Any]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)

    @contextlib.contextmanager
    def _file_lock(self):
        if self.path is None:
            with self._thread_lock:
                yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with open(lock_path, "a+", encoding="utf-8") as handle:
            with self._thread_lock:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _refresh(self, state: dict[str, Any], now: float) -> None:
        today = datetime.fromtimestamp(now).date().isoformat()
        if state.get("date") != today:
            state.update(_empty_state(today))
        hour_at = float(state.get("hour_at") or 0)
        if now - hour_at >= 3600:
            state["hour_at"] = now
            state["read_hour"] = 0
            state["write_hour"] = 0

    # ------------------------------------------------------------------ 请求
    async def acquire(self, kind: str = "read") -> None:
        if not self.enabled:
            return
        async with self._async_lock:
            wait = self._reserve(kind)
            if wait > 0:
                log.info("请求保护：等待 %.1fs 再发下一个请求", wait)
                await asyncio.sleep(wait)
            self._mark_sent()

    def _reserve(self, kind: str) -> float:
        now = self.clock()
        with self._file_lock():
            state = self._load()
            self._refresh(state, now)
            until = float(state.get("until") or 0)
            if until > now:
                remaining = (until - now) / 60
                raise TrafficPaused(
                    f"请求已暂停（{state.get('reason') or '平台限制'}），约 {remaining:.0f} 分钟后再试"
                )
            writing = kind == "write"
            if writing:
                gap = self.settings.write_gap_seconds
                last_write = float(state.get("last_write") or 0)
                if gap and now - last_write < gap:
                    raise TrafficPaused(f"写操作冷却中，还需 {gap - (now - last_write):.0f} 秒")
                if state["write_hour"] >= self.settings.write_hourly:
                    raise TrafficPaused("已达到每小时写操作预算")
                if state["write_day"] >= self.settings.write_daily:
                    raise TrafficPaused("已达到每日写操作预算")
            else:
                if state["read_hour"] >= self.settings.read_hourly:
                    raise TrafficPaused("已达到每小时读预算")
                if state["read_day"] >= self.settings.read_daily:
                    raise TrafficPaused("已达到每日读预算")

            last_at = float(state.get("last_at") or 0)
            wait = max(0.0, self.settings.min_gap_ms / 1000 - (now - last_at))
            self._save(state)
            return wait

    def _mark_sent(self) -> None:
        now = self.clock()
        with self._file_lock():
            state = self._load()
            self._refresh(state, now)
            state["last_at"] = now
            self._save(state)

    def note_response(self, kind: str, status: int) -> None:
        """记录一次真实响应：2xx 清零连击，412 触发递增暂停。"""
        if not self.enabled:
            return
        now = self.clock()
        with self._file_lock():
            state = self._load()
            self._refresh(state, now)
            if status == 412:
                last_412 = float(state.get("last_412") or 0)
                strikes = int(state.get("strikes") or 0)
                strikes = strikes + 1 if now - last_412 < 86400 else 1
                pause = PAUSE_MINUTES[min(strikes - 1, len(PAUSE_MINUTES) - 1)] * 60
                state["strikes"] = strikes
                state["last_412"] = now
                state["until"] = now + pause
                state["reason"] = f"B 站返回 412（第 {strikes} 次，暂停 {pause // 60} 分钟）"
                log.warning("B 站风控：%s", state["reason"])
            elif 200 <= status < 300:
                state["strikes"] = 0
            if kind == "write":
                state["write_hour"] = int(state.get("write_hour") or 0) + 1
                state["write_day"] = int(state.get("write_day") or 0) + 1
                state["last_write"] = now
            else:
                state["read_hour"] = int(state.get("read_hour") or 0) + 1
                state["read_day"] = int(state.get("read_day") or 0) + 1
            self._save(state)

    # ------------------------------------------------------------------ 查询
    def status(self) -> dict[str, Any]:
        now = self.clock()
        with self._file_lock():
            state = self._load()
            self._refresh(state, now)
            self._save(state)
        return {
            "date": state["date"],
            "read_hour": state["read_hour"],
            "read_day": state["read_day"],
            "write_hour": state["write_hour"],
            "write_day": state["write_day"],
            "paused_until": state["until"],
            "pause_reason": state["reason"],
            "strikes": state["strikes"],
            "settings": {
                "min_gap_ms": self.settings.min_gap_ms,
                "read_hourly": self.settings.read_hourly,
                "read_daily": self.settings.read_daily,
                "write_hourly": self.settings.write_hourly,
                "write_daily": self.settings.write_daily,
            },
        }

    def resume(self) -> None:
        """手动解除暂停；当天内的 412 连击次数保留，避免刚恢复就被打回。"""
        with self._file_lock():
            state = self._load()
            state["until"] = 0.0
            state["reason"] = ""
            self._save(state)


def guard_from_config(config: Any, path: str | Path | None = None) -> TrafficGuard:
    settings = TrafficSettings(
        min_gap_ms=int(getattr(config, "bili_min_interval", 0.6) * 1000),
        read_hourly=int(getattr(config, "bili_read_hourly", 600)),
        read_daily=int(getattr(config, "bili_read_daily", 3000)),
        write_hourly=int(getattr(config, "bili_write_hourly", 12)),
        write_daily=int(getattr(config, "bili_write_daily", 30)),
        write_gap_seconds=int(getattr(config, "bili_write_gap_seconds", 120)),
    )
    return TrafficGuard(
        path,
        settings,
        enabled=bool(getattr(config, "traffic_guard_enabled", True)),
    )
