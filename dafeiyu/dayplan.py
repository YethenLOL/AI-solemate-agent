"""生物钟：在作息窗口里随机挑时间醒来，允许「今天不想看」。

计划一旦生成就持久化（绝对时间戳），重启后仍按原计划执行；
改作息才会重排，普通重启不会重新抽签。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Callable


@dataclass(slots=True)
class Window:
    start: int  # 分钟
    end: int  # 分钟，跨午夜时会 > 1440
    wakes: int
    label: str
    wrap: bool


@dataclass(slots=True)
class Occurrence:
    key: str
    window: Window
    starts_at: float
    ends_at: float


def parse_time(value: str) -> int:
    text = (value or "").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        raise ValueError(f"无效作息时间：{value!r}") from None
    if hour > 24 or minute > 59 or (hour == 24 and minute != 0) or hour < 0 or minute < 0:
        raise ValueError(f"无效作息时间：{value!r}")
    return hour * 60 + minute


def parse_windows(spec: str, wakes: int = 1) -> list[Window]:
    """支持 `20:00-24:00`、`20:00-23:00x2,12:00-13:00` 这种写法。"""
    text = (spec or "").replace("，", ",").strip()
    if not text:
        text = "20:00-24:00"
    windows: list[Window] = []
    for chunk in text.split(","):
        part = chunk.strip()
        if not part:
            continue
        count = wakes
        if "x" in part:
            part, count_text = part.rsplit("x", 1)
            try:
                count = int(count_text)
            except ValueError:
                raise ValueError(f"无效作息次数：{chunk!r}") from None
        if "-" not in part:
            raise ValueError(f"无效作息窗口：{chunk!r}（应形如 20:00-24:00）")
        start_text, end_text = part.split("-", 1)
        start = parse_time(start_text)
        raw_end = parse_time(end_text)
        end = raw_end + 1440 if raw_end <= start else raw_end
        windows.append(
            Window(
                start=start,
                end=end,
                wakes=min(12, max(0, count)),
                label=f"{start_text.strip()}-{end_text.strip()}",
                wrap=end > 1440,
            )
        )
    if not windows:
        raise ValueError("作息窗口不能为空")
    return windows


def max_wakes(windows: list[Window]) -> int:
    return min(12, sum(window.wakes for window in windows))


def _stamp(day: date, minutes: int) -> float:
    base = datetime.combine(day, dtime.min)
    return (base + timedelta(minutes=minutes)).timestamp()


def occurrences(windows: list[Window], now: float) -> list[Occurrence]:
    today = datetime.fromtimestamp(now).date()
    result: list[Occurrence] = []
    for window in windows:
        days = [today - timedelta(days=1), today] if window.wrap else [today]
        for day in days:
            occurrence = Occurrence(
                key=f"{day.isoformat()}|{window.label}",
                window=window,
                starts_at=_stamp(day, window.start),
                ends_at=_stamp(day, window.end),
            )
            if occurrence.ends_at > now:
                result.append(occurrence)
    return result


def _fresh_state(day: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "date": day,
        "wakes": 0,
        "woke_at": None,
        "used": {},
        "action": "done",
        "fired": False,
        "at": None,
        "ends_at": None,
        "window": "",
        "window_key": "",
        "at_text": "",
        "reason": "",
    }


def is_pending(state: dict[str, Any] | None, now: float) -> bool:
    if not state or state.get("schema") != 1:
        return False
    if state.get("action") not in {"wake", "skip"} or state.get("fired"):
        return False
    at, ends = state.get("at"), state.get("ends_at")
    return isinstance(at, (int, float)) and isinstance(ends, (int, float)) and ends > now


def _history(state: dict[str, Any] | None, now: float) -> dict[str, Any]:
    today = datetime.fromtimestamp(now).date().isoformat()
    base = _fresh_state(today)
    if not state or state.get("schema") != 1:
        return base
    if state.get("date") != today:
        return base
    used = {
        key: int(count)
        for key, count in (state.get("used") or {}).items()
        if isinstance(key, str) and key[:10] >= (datetime.fromtimestamp(now).date() - timedelta(days=3)).isoformat()
    }
    base.update(
        wakes=int(state.get("wakes") or 0),
        woke_at=state.get("woke_at"),
        used=used,
    )
    return base


def make_plan(
    windows: list[Window],
    *,
    skip_chance: float = 0.3,
    min_gap_minutes: int = 10,
    state: dict[str, Any] | None = None,
    now: float | None = None,
    rng: Callable[[], float] = random.random,
) -> dict[str, Any]:
    now = now if now is not None else datetime.now().timestamp()
    if is_pending(state, now):
        return state  # type: ignore[return-value]

    history = _history(state, now)
    if history["wakes"] >= max_wakes(windows):
        history.update(action="done", fired=True, reason="今天醒来次数已用完")
        return history

    pool = [
        occurrence
        for occurrence in occurrences(windows, now)
        if occurrence.window.wakes > history["used"].get(occurrence.key, 0)
    ]
    pool.sort(key=lambda item: (item.ends_at, item.starts_at))
    if not pool:
        history.update(action="done", fired=True, reason="今天可用窗口已结束")
        return history

    gap = max(60, min_gap_minutes * 60)
    last_woke = 0.0
    if history.get("woke_at"):
        try:
            last_woke = datetime.fromisoformat(str(history["woke_at"])).timestamp()
        except ValueError:
            last_woke = 0.0

    picked: tuple[Occurrence, float] | None = None
    for occurrence in pool:
        earliest = max(now, occurrence.starts_at, last_woke + gap)
        if earliest >= occurrence.ends_at - 30:
            continue
        picked = (occurrence, earliest)
        break
    if picked is None:
        history.update(action="done", fired=True, reason="窗口里排不下下一次（间隔不够）")
        return history

    occurrence, earliest = picked
    at = earliest + rng() * max(1.0, occurrence.ends_at - earliest)
    at_dt = datetime.fromtimestamp(at)
    skip = rng() < min(1.0, max(0.0, skip_chance))
    history.update(
        action="skip" if skip else "wake",
        fired=False,
        at=at,
        ends_at=occurrence.ends_at,
        window=occurrence.window.label,
        window_key=occurrence.key,
        at_text=at_dt.strftime("%Y-%m-%d %H:%M"),
        reason="本次计划跳过看视频，仍写日记" if skip else "",
    )
    return history


def complete_plan(state: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    if state.get("fired"):
        return state
    now = now if now is not None else datetime.now().timestamp()
    history = _history(state, now)
    key = str(state.get("window_key") or "")
    if key:
        history["used"][key] = history["used"].get(key, 0) + 1
        history["wakes"] += 1
    history.update(
        action=state.get("action") or "done",
        fired=True,
        at=state.get("at"),
        ends_at=state.get("ends_at"),
        window=state.get("window", ""),
        window_key=key,
        at_text=state.get("at_text", ""),
        reason=state.get("reason", ""),
        woke_at=datetime.fromtimestamp(now).isoformat(timespec="seconds"),
    )
    return history


def plan_text(state: dict[str, Any] | None) -> str:
    if not state:
        return "还没排计划"
    action = state.get("action")
    if action == "done":
        return f"今天收工：{state.get('reason') or '没有更多窗口'}"
    if state.get("fired"):
        return f"今天已醒 {state.get('wakes', 0)} 次"
    at_text = state.get("at_text") or "?"
    label = "看视频" if action == "wake" else "今天不想看（仍写日记）"
    return f"下次 {at_text} · {label}"
