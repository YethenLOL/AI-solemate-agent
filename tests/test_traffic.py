from __future__ import annotations

from pathlib import Path

import pytest

from dafeiyu.traffic import TrafficGuard, TrafficPaused, TrafficSettings


class Clock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_guard(tmp_path: Path, clock: Clock, **overrides) -> TrafficGuard:
    settings = TrafficSettings(**overrides)
    return TrafficGuard(tmp_path / "traffic.json", settings, clock=clock)


async def test_read_budget_and_pause(tmp_path: Path) -> None:
    clock = Clock()
    guard = make_guard(tmp_path, clock, min_gap_ms=0, read_daily=2)
    await guard.acquire("read")
    guard.note_response("read", 200)
    await guard.acquire("read")
    guard.note_response("read", 200)

    with pytest.raises(TrafficPaused, match="每日读预算"):
        await guard.acquire("read")

    clock.advance(86401)
    guard.note_response("read", 412)
    status = guard.status()
    assert status["paused_until"] > clock.now
    assert "412" in status["pause_reason"]

    with pytest.raises(TrafficPaused, match="暂停"):
        await guard.acquire("read")

    clock.advance(600)
    guard.resume()
    await guard.acquire("read")


async def test_hourly_reset(tmp_path: Path) -> None:
    clock = Clock()
    guard = make_guard(tmp_path, clock, min_gap_ms=0, read_hourly=1, read_daily=10)
    await guard.acquire("read")
    guard.note_response("read", 200)
    with pytest.raises(TrafficPaused, match="每小时"):
        await guard.acquire("read")
    clock.advance(3601)
    await guard.acquire("read")


async def test_write_cooldown(tmp_path: Path) -> None:
    clock = Clock()
    guard = make_guard(tmp_path, clock, min_gap_ms=0, write_gap_seconds=120)
    await guard.acquire("write")
    guard.note_response("write", 200)
    with pytest.raises(TrafficPaused, match="冷却"):
        await guard.acquire("write")
    clock.advance(121)
    await guard.acquire("write")


async def test_412_pause_escalates_and_resets_after_success(tmp_path: Path) -> None:
    clock = Clock()
    guard = make_guard(tmp_path, clock, min_gap_ms=0)
    guard.note_response("read", 412)
    first = guard.status()["paused_until"] - clock.now
    guard.resume()
    guard.note_response("read", 412)
    second = guard.status()["paused_until"] - clock.now
    assert second > first

    guard.resume()
    guard.note_response("read", 200)
    guard.note_response("read", 412)
    third = guard.status()["paused_until"] - clock.now
    assert third == first


def test_disabled_guard_is_noop(tmp_path: Path) -> None:
    guard = TrafficGuard(tmp_path / "traffic.json", TrafficSettings(), enabled=False)
    guard.note_response("read", 412)
    assert guard.status()["paused_until"] == 0
