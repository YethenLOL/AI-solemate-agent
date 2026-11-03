from __future__ import annotations

from datetime import datetime

from dafeiyu.usage import (
    aggregate,
    bucket_starts,
    estimate_tokens,
    human_tokens,
    period_start,
)

NOW = datetime(2026, 11, 3, 19, 30)  # 周二


def make_row(at: datetime, prompt: int, completion: int, estimated: bool = False) -> dict:
    return {
        "created_at": at.timestamp(),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "estimated": estimated,
    }


def test_period_start() -> None:
    assert period_start("day", NOW) == datetime(2026, 11, 3)
    assert period_start("week", NOW) == datetime(2026, 11, 2)  # 周一
    assert period_start("month", NOW) == datetime(2026, 11, 1)


def test_bucket_starts() -> None:
    days = bucket_starts("day", NOW, 7)
    assert days[0] == datetime(2026, 10, 28)
    assert days[-1] == datetime(2026, 11, 3)

    weeks = bucket_starts("week", NOW, 3)
    assert weeks == [datetime(2026, 10, 19), datetime(2026, 10, 26), datetime(2026, 11, 2)]

    months = bucket_starts("month", NOW, 3)
    assert months == [datetime(2026, 9, 1), datetime(2026, 10, 1), datetime(2026, 11, 1)]


def test_aggregate_day_totals_only_current_day() -> None:
    rows = [
        make_row(datetime(2026, 11, 3, 9, 0), 100, 20),
        make_row(datetime(2026, 11, 3, 10, 0), 10, 5),
        make_row(datetime(2026, 11, 2, 9, 0), 50, 10, estimated=True),
        make_row(datetime(2026, 10, 30, 9, 0), 30, 5),
    ]
    totals, buckets = aggregate(rows, "day", 7, NOW)

    assert len(buckets) == 7
    assert totals.total == 135
    assert totals.prompt == 110
    assert totals.completion == 25
    assert totals.calls == 2
    assert totals.estimated_calls == 0

    assert buckets[-1].total == 135
    assert buckets[-2].total == 60
    assert buckets[2].total == 35  # 10/30
    assert buckets[0].total == 0
    assert [bucket.label for bucket in buckets][-1] == "11/3"


def test_aggregate_week_and_month() -> None:
    rows = [
        make_row(datetime(2026, 11, 3, 9, 0), 100, 20),
        make_row(datetime(2026, 10, 30, 9, 0), 50, 10),  # 上周
        make_row(datetime(2026, 10, 20, 9, 0), 30, 5),  # 两周前
    ]
    week_totals, week_buckets = aggregate(rows, "week", 3, NOW)
    assert week_totals.total == 120
    assert [bucket.total for bucket in week_buckets] == [35, 60, 120]

    month_totals, month_buckets = aggregate(rows, "month", 3, NOW)
    assert month_totals.total == 120
    assert [bucket.total for bucket in month_buckets] == [0, 95, 120]
    assert month_buckets[-1].label == "2026-11"


def test_aggregate_ignores_bad_rows() -> None:
    totals, buckets = aggregate(
        [{"created_at": "oops", "prompt_tokens": 1, "completion_tokens": 1}], "day", 2, NOW
    )
    assert totals.total == 0
    assert all(bucket.total == 0 for bucket in buckets)


def test_estimate_tokens_and_human_format() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好") == 2
    assert estimate_tokens("hello world") == 2
    assert human_tokens(999) == "999"
    assert human_tokens(1500) == "1.5k"
    assert human_tokens(2_000_000) == "2.00M"
