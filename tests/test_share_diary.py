from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from dafeiyu.diary import DiaryStore
from dafeiyu.share import (
    can_share,
    quota_for_silence,
    silence_days,
    validate,
)


class FakeMemory:
    def __init__(self, count: int = 0, shared: set[str] | None = None) -> None:
        self.count = count
        self.shared = shared or set()

    def share_count_since(self, since: float) -> int:
        return self.count

    def has_shared_since(self, bvid: str, since: float) -> bool:
        return bvid in self.shared


def test_validate_score_reason_rank() -> None:
    assert validate(15, "这条讲得很清楚，值得看", 1) == (15, "这条讲得很清楚，值得看")
    with pytest.raises(ValueError, match="阈值"):
        validate(12, "这条讲得很清楚，值得看", 1)
    with pytest.raises(ValueError, match="8 个字"):
        validate(15, "太短", 1)
    with pytest.raises(ValueError, match="第一名"):
        validate(15, "这条讲得很清楚，值得看", 2)
    with pytest.raises(ValueError, match="0–20"):
        validate(21, "这条讲得很清楚，值得看", 1)


def test_silence_quota() -> None:
    now = 1_700_000_000
    assert silence_days(None, now) == 0
    assert quota_for_silence(0) == 3
    assert quota_for_silence(2) == 3
    assert quota_for_silence(3) == 1
    assert quota_for_silence(7) == 0
    assert silence_days(now - 3.5 * 86400, now) == 3


def test_can_share_daily_and_same_video() -> None:
    now = 1_700_000_000
    ok = can_share(
        memory=FakeMemory(),
        bvid="BV1xx411c7mD",
        score=15,
        reason="这条讲得很清楚，值得看",
        now=now,
    )
    assert ok.ok and ok.quota == 3

    exhausted = can_share(
        memory=FakeMemory(count=3),
        bvid="BV1xx411c7mD",
        score=15,
        reason="这条讲得很清楚，值得看",
        now=now,
    )
    assert not exhausted.ok and "额度" in exhausted.reason

    repeated = can_share(
        memory=FakeMemory(shared={"BV1xx411c7mD"}),
        bvid="BV1xx411c7mD",
        score=15,
        reason="这条讲得很清楚，值得看",
        now=now,
    )
    assert not repeated.ok and "7 天" in repeated.reason


def test_can_share_respects_silence() -> None:
    now = 1_700_000_000
    quiet = can_share(
        memory=FakeMemory(),
        bvid="BV1xx411c7mD",
        score=15,
        reason="这条讲得很清楚，值得看",
        last_owner_at=now - 4 * 86400,
        now=now,
    )
    assert quiet.ok and quiet.quota == 1

    silent = can_share(
        memory=FakeMemory(),
        bvid="BV1xx411c7mD",
        score=15,
        reason="这条讲得很清楚，值得看",
        last_owner_at=now - 8 * 86400,
        now=now,
    )
    assert not silent.ok and "不分享" in silent.reason


def test_diary_append_read_and_recent(tmp_path: Path) -> None:
    store = DiaryStore(tmp_path / "diary")
    assert store.read("owner") == ""
    store.append("owner", "今天看到一条讲 STM32 的视频。", now=datetime(2026, 11, 3, 21, 0))
    store.append("private", "其实有点累。", now=datetime(2026, 11, 3, 21, 1))
    store.append("owner", "第二天又学到一点。", now=datetime(2026, 11, 4, 22, 0))

    text = store.read("owner")
    assert text.startswith("# 给主人的信")
    assert text.count("## 2026-11-03") == 1
    assert "STM32" in text
    assert store.latest("owner").startswith("## 2026-11-04")

    recent = store.recent(days=1)
    assert "第二天" in recent["owner"]
    assert "第二天" not in recent["private"]
    assert "其实有点累" in recent["private"]

    with pytest.raises(ValueError):
        store.append("owner", "   ")
    with pytest.raises(ValueError):
        store.path_for("unknown")
