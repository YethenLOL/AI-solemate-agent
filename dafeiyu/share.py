"""分享配额与沉默衰减。

参考 dsh-bili-whale 的硬限制：
- 评分 0–20，必须 ≥ 阈值（默认 13）
- 理由至少 8 个字
- 只允许排名第 1 的视频
- 每天合计 ≤ 3 条
- 同一个 BV 7 天内只分享一次
- 主人连续 3 天没回应 → 每天最多 1 条；7 天 → 0 条（日记照写）
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

MIN_REASON_CHARS = 8
DEFAULT_MIN_SCORE = 13
DEFAULT_DAILY_LIMIT = 3
SAME_VIDEO_DAYS = 7
SILENT_AFTER_DAYS = 3
SILENT_STOP_DAYS = 7


@dataclass(slots=True)
class ShareDecision:
    ok: bool
    reason: str
    quota: int = 0


def validate(score: Any, reason: str, rank: Any = 1, *, min_score: int = DEFAULT_MIN_SCORE) -> tuple[int, str]:
    try:
        value = int(score)
    except (TypeError, ValueError):
        raise ValueError("分享需要 0–20 的评分") from None
    if value < 0 or value > 20:
        raise ValueError("分享评分必须在 0–20 之间")
    text = (reason or "").strip()
    if len(text) < MIN_REASON_CHARS:
        raise ValueError(f"分享理由至少 {MIN_REASON_CHARS} 个字")
    try:
        rank_value = int(rank)
    except (TypeError, ValueError):
        raise ValueError("分享只允许第一名") from None
    if rank_value != 1:
        raise ValueError("分享只允许第一名")
    if value < min_score:
        raise ValueError(f"评分 {value} 未达到阈值 {min_score}")
    return value, text


def silence_days(last_owner_at: float | None, now: float | None = None) -> int:
    if not last_owner_at:
        return 0
    now = now if now is not None else time.time()
    return max(0, int((now - last_owner_at) // 86400))


def quota_for_silence(
    days: int,
    *,
    daily_limit: int = DEFAULT_DAILY_LIMIT,
    silent_after: int = SILENT_AFTER_DAYS,
    silent_stop: int = SILENT_STOP_DAYS,
) -> int:
    if days >= silent_stop:
        return 0
    if days >= silent_after:
        return min(1, daily_limit)
    return daily_limit


def can_share(
    *,
    memory: Any,
    bvid: str,
    score: Any,
    reason: str,
    rank: Any = 1,
    last_owner_at: float | None = None,
    now: float | None = None,
    min_score: int = DEFAULT_MIN_SCORE,
    daily_limit: int = DEFAULT_DAILY_LIMIT,
    silent_after: int = SILENT_AFTER_DAYS,
    silent_stop: int = SILENT_STOP_DAYS,
) -> ShareDecision:
    now = now if now is not None else time.time()
    try:
        value, text = validate(score, reason, rank, min_score=min_score)
    except ValueError as exc:
        return ShareDecision(False, str(exc))

    days = silence_days(last_owner_at, now)
    quota = quota_for_silence(
        days,
        daily_limit=daily_limit,
        silent_after=silent_after,
        silent_stop=silent_stop,
    )
    if quota <= 0:
        return ShareDecision(False, f"主人已经 {days} 天没回应，今天不分享", quota)

    today_start = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    used = int(memory.share_count_since(today_start))
    if used >= quota:
        return ShareDecision(False, f"今天的分享额度已用完（{used}/{quota}）", quota)

    if memory.has_shared_since(bvid, now - SAME_VIDEO_DAYS * 86400):
        return ShareDecision(False, f"这个视频 {SAME_VIDEO_DAYS} 天内已经分享过了", quota)

    return ShareDecision(True, text, quota)
