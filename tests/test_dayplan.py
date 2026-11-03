from __future__ import annotations

from datetime import datetime

from dafeiyu.dayplan import (
    complete_plan,
    is_pending,
    make_plan,
    max_wakes,
    parse_windows,
    plan_text,
)

NOW = datetime(2026, 11, 3, 19, 0).timestamp()


def seq(values):
    iterator = iter(values)
    return lambda: next(iterator)


def test_parse_windows_and_max_wakes() -> None:
    windows = parse_windows("20:00-24:00x2, 12:30-13:00", wakes=1)
    assert [window.label for window in windows] == ["20:00-24:00", "12:30-13:00"]
    assert [window.wakes for window in windows] == [2, 1]
    assert windows[0].wrap is False
    assert max_wakes(windows) == 3
    assert max_wakes(parse_windows("23:00-01:00", 1)) == 1


def test_make_plan_picks_time_in_window_and_can_skip() -> None:
    windows = parse_windows("20:00-24:00", 1)
    state = make_plan(
        windows,
        skip_chance=0.3,
        min_gap_minutes=10,
        state=None,
        now=NOW,
        rng=seq([0.5, 0.1]),
    )
    assert state["action"] == "skip"
    assert state["fired"] is False
    assert NOW <= state["at"] < datetime(2026, 11, 4, 0, 0).timestamp()
    assert state["at_text"].startswith("2026-11-03 22:")
    assert is_pending(state, NOW)
    assert "不想看" in plan_text(state)


def test_plan_is_stable_until_fired_and_obeys_min_gap() -> None:
    windows = parse_windows("20:00-24:00", 2)
    first = make_plan(windows, skip_chance=0.0, min_gap_minutes=10, now=NOW, rng=seq([0.1, 0.9]))
    assert make_plan(windows, skip_chance=0.0, min_gap_minutes=10, state=first, now=NOW + 60) is first
    fired = complete_plan(first)
    assert fired["fired"] and fired["wakes"] == 1

    second = make_plan(windows, skip_chance=0.0, min_gap_minutes=10, state=fired, now=NOW + 60)
    assert second["action"] == "wake"
    assert second["at"] >= datetime.fromisoformat(fired["woke_at"]).timestamp() + 600


def test_cross_midnight_window() -> None:
    windows = parse_windows("23:00-01:00", 1)
    now = datetime(2026, 11, 3, 23, 30).timestamp()
    state = make_plan(windows, skip_chance=0.0, now=now, rng=seq([0.5, 0.9]))
    assert state["window"] == "23:00-01:00"
    assert state["ends_at"] == datetime(2026, 11, 4, 1, 0).timestamp()
    assert now <= state["at"] < state["ends_at"]


def test_done_when_all_wakes_used() -> None:
    windows = parse_windows("20:00-24:00", 1)
    state = make_plan(windows, skip_chance=0.0, now=NOW, rng=seq([0.5, 0.9]))
    state = complete_plan(state)
    state = make_plan(windows, skip_chance=0.0, state=state, now=NOW + 60)
    assert state["action"] == "done"
    assert "收工" in plan_text(state)


def test_bad_window_is_rejected() -> None:
    for spec in ("25:00-26:00", "20:00", "abc"):
        try:
            parse_windows(spec)
        except ValueError:
            continue
        raise AssertionError(f"{spec} 应该报错")
