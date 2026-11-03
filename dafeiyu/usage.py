"""Token 用量统计：按日 / 周 / 月聚合，供控制台展示。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Literal

Period = Literal["day", "week", "month"]
PERIOD_BUCKETS: dict[str, int] = {"day": 7, "week": 8, "month": 6}


@dataclass(slots=True)
class UsageTotals:
    prompt: int = 0
    completion: int = 0
    calls: int = 0
    estimated_calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def add(self, prompt: int, completion: int, *, estimated: bool = False) -> None:
        self.prompt += prompt
        self.completion += completion
        self.calls += 1
        if estimated:
            self.estimated_calls += 1


@dataclass(slots=True)
class UsageBucket:
    start: datetime
    label: str
    prompt: int = 0
    completion: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


def estimate_tokens(text: str) -> int:
    """粗略估算 token：CJK 约 1 字 1 token，其余按 4 字符 1 token。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u3400" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff")
    other = len(text) - cjk
    return max(1, int(cjk + other / 4))


def period_start(period: Period, now: datetime) -> datetime:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return day
    if period == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def bucket_starts(period: Period, now: datetime, count: int) -> list[datetime]:
    starts = [period_start(period, now)]
    for _ in range(max(count, 1) - 1):
        last = starts[-1]
        if period == "day":
            previous = last - timedelta(days=1)
        elif period == "week":
            previous = last - timedelta(days=7)
        else:
            previous = (last - timedelta(days=1)).replace(day=1)
        starts.append(previous)
    return list(reversed(starts))


def bucket_label(period: Period, start: datetime) -> str:
    if period == "month":
        return f"{start.year}-{start.month:02d}"
    return f"{start.month}/{start.day}"


def aggregate(
    rows: Iterable[dict[str, Any]],
    period: Period,
    count: int | None = None,
    now: datetime | None = None,
) -> tuple[UsageTotals, list[UsageBucket]]:
    """返回「当前周期合计」和「最近 count 个周期的分桶」。"""
    now = now or datetime.now()
    count = count or PERIOD_BUCKETS.get(period, 7)
    starts = bucket_starts(period, now, count)
    buckets = [UsageBucket(start=start, label=bucket_label(period, start)) for start in starts]
    current = UsageTotals()
    current_start = starts[-1]

    for row in rows:
        try:
            at = datetime.fromtimestamp(float(row.get("created_at") or 0))
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        prompt = _positive_int(row.get("prompt_tokens"))
        completion = _positive_int(row.get("completion_tokens"))
        estimated = bool(row.get("estimated"))
        if at >= current_start:
            current.add(prompt, completion, estimated=estimated)
        for bucket in reversed(buckets):
            if at >= bucket.start:
                bucket.prompt += prompt
                bucket.completion += completion
                bucket.calls += 1
                break
    return current, buckets


def _positive_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def human_tokens(value: int) -> str:
    number = float(max(value, 0))
    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}k"
    return str(int(number))
