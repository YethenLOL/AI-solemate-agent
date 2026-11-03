from __future__ import annotations

from pathlib import Path

from dafeiyu.activity import ActivityMonitor
from dafeiyu.config import Config
from dafeiyu.memory import Memory


class FakeComputer:
    is_macos = True

    def __init__(self, app: str = "Safari", title: str = "网页", idle: int = 5) -> None:
        self.app = app
        self.title = title
        self.idle = idle

    async def get_status(self) -> dict:
        return {"ok": True, "app": self.app, "window": self.title, "idle_seconds": self.idle}


def make_monitor(tmp_path: Path, **overrides) -> tuple[ActivityMonitor, Memory, FakeComputer]:
    memory = Memory(tmp_path / "activity.db").connect()
    config = Config(
        llm_api_key="x",
        activity_enabled=True,
        activity_interval_seconds=45,
        activity_idle_skip_seconds=900,
        activity_blocklist=("1Password",),
        **overrides,
    )
    computer = FakeComputer()
    monitor = ActivityMonitor(config=config, memory=memory, computer=computer)
    return monitor, memory, computer


async def test_sample_records_only_changes(tmp_path: Path) -> None:
    monitor, memory, computer = make_monitor(tmp_path)
    event = await monitor.sample()
    assert event and event["app"] == "Safari"
    assert memory.count_activity() == 1
    assert await monitor.sample() is None
    assert memory.count_activity() == 1

    computer.app = "Code"
    computer.title = "main.py"
    assert await monitor.sample() is not None
    assert memory.count_activity() == 2
    recent = memory.recent_activity(5)
    assert [item["app"] for item in recent] == ["Safari", "Code"]
    memory.close()


async def test_skip_blocked_private_and_idle(tmp_path: Path) -> None:
    monitor, memory, computer = make_monitor(tmp_path)

    computer.app = "1Password"
    assert await monitor.sample() is None

    computer.app = "Safari"
    computer.title = "无痕浏览"
    assert await monitor.sample() is None

    computer.title = "正常网页"
    computer.idle = 1000
    assert await monitor.sample() is None
    assert memory.count_activity() == 0
    memory.close()
