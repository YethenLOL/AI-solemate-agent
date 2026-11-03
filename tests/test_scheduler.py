from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dafeiyu.agent import ProactiveMessage
from dafeiyu.bilibili import Video
from dafeiyu.config import Config
from dafeiyu.memory import Memory
from dafeiyu.scheduler import BrowseLoop, ProactiveScheduler


class FakeBot:
    def __init__(self) -> None:
        self.private: list[tuple[int, str]] = []
        self.images: list[tuple[int, str]] = []

    async def send_private(self, user_id: int, text: str) -> None:
        self.private.append((user_id, text))

    async def send_image_private(self, user_id: int, url: str) -> None:
        self.images.append((user_id, url))


class FakeAgent:
    def __init__(self, message: ProactiveMessage | None) -> None:
        self.message = message
        self.kinds: list[str] = []
        self.outgoing: list[str] = []

    async def compose_proactive(self, session_id: str, kind: str = "browse") -> ProactiveMessage | None:
        self.kinds.append(kind)
        return self.message

    def record_outgoing(self, session_id: str, text: str) -> None:
        self.outgoing.append(text)


def make_scheduler(
    tmp_path: Path,
    *,
    message: ProactiveMessage | None = None,
    **overrides: Any,
) -> tuple[ProactiveScheduler, FakeBot, FakeAgent, Memory]:
    memory = Memory(tmp_path / "scheduler.db").connect()
    settings: dict[str, Any] = {
        "llm_api_key": "sk-test",
        "owner_qq": 1,
        "quiet_hours": None,
        "proactive_min_minutes": 50,
        "proactive_max_minutes": 140,
        "proactive_daily_limit": 2,
    }
    settings.update(overrides)
    config = Config(**settings)
    bot = FakeBot()
    agent = FakeAgent(message or ProactiveMessage(text="咕噜，在吗"))
    scheduler = ProactiveScheduler(
        config=config,
        agent=agent,  # type: ignore[arg-type]
        bot=bot,  # type: ignore[arg-type]
        memory=memory,
    )
    return scheduler, bot, agent, memory


async def test_sends_proactive_message(tmp_path: Path) -> None:
    message = ProactiveMessage(text="快看这个", bvid="BV1xx411c7mD", cover="https://cover")
    scheduler, bot, agent, memory = make_scheduler(tmp_path, message=message)
    memory.upsert_video(bvid="BV1xx411c7mD", title="鱼的一天", author="UP")
    memory.set_state("next_proactive_at", time.time() - 1)

    result = await scheduler.maybe_send()

    assert result is message
    assert bot.private == [(1, "快看这个")]
    assert agent.kinds == ["browse"]
    assert agent.outgoing == ["快看这个"]
    assert memory.unshared_videos() == []
    assert memory.get_float("last_proactive_at") > 0
    assert memory.get_float("next_proactive_at") > time.time()
    assert memory.get_int(f"proactive_count:{scheduler._today()}") == 1
    memory.close()


async def test_falls_back_to_smalltalk_without_videos(tmp_path: Path) -> None:
    scheduler, bot, agent, memory = make_scheduler(tmp_path)
    memory.set_state("next_proactive_at", time.time() - 1)
    await scheduler.maybe_send()
    assert agent.kinds == ["chat"]
    assert len(bot.private) == 1
    memory.close()


async def test_sends_cover_when_enabled(tmp_path: Path) -> None:
    message = ProactiveMessage(text="看", bvid="BV1", cover="https://cover")
    scheduler, bot, _, memory = make_scheduler(tmp_path, message=message, share_cover=True)
    memory.set_state("next_proactive_at", time.time() - 1)
    await scheduler.maybe_send()
    assert bot.images == [(1, "https://cover")]
    memory.close()


async def test_force_send_bypasses_gates(tmp_path: Path) -> None:
    scheduler, bot, agent, memory = make_scheduler(tmp_path, quiet_hours=(0, 24))
    memory.set_state(f"proactive_count:{scheduler._today()}", 99)
    result = await scheduler.maybe_send(force=True)
    assert result is not None
    assert bot.private == [(1, result.text)]
    memory.close()


async def test_skip_when_paused(tmp_path: Path) -> None:
    scheduler, bot, agent, memory = make_scheduler(tmp_path)
    memory.set_state("next_proactive_at", time.time() - 1)
    memory.set_state("proactive_paused_until", time.time() + 600)
    assert await scheduler.maybe_send() is None
    assert bot.private == []
    memory.close()


async def test_daily_limit(tmp_path: Path) -> None:
    scheduler, bot, agent, memory = make_scheduler(tmp_path)
    memory.set_state("next_proactive_at", time.time() - 1)
    memory.set_state(f"proactive_count:{scheduler._today()}", 2)
    assert await scheduler.maybe_send() is None
    assert bot.private == []
    memory.close()


async def test_recent_user_activity_postpones(tmp_path: Path) -> None:
    scheduler, bot, agent, memory = make_scheduler(tmp_path)
    memory.set_state("next_proactive_at", time.time() - 1)
    memory.set_state("last_user_ts:private:1", time.time() - 60)
    assert await scheduler.maybe_send() is None
    assert bot.private == []
    assert memory.get_float("next_proactive_at") > time.time()
    memory.close()


async def test_no_owner(tmp_path: Path) -> None:
    scheduler, bot, agent, memory = make_scheduler(tmp_path, owner_qq=None)
    memory.set_state("next_proactive_at", time.time() - 1)
    assert await scheduler.maybe_send() is None
    memory.close()


def test_quiet_hours(tmp_path: Path) -> None:
    scheduler, _, _, memory = make_scheduler(tmp_path, quiet_hours=(23, 7))
    assert scheduler.in_quiet_hours(datetime(2026, 1, 1, 23, 30))
    assert scheduler.in_quiet_hours(datetime(2026, 1, 1, 3, 0))
    assert not scheduler.in_quiet_hours(datetime(2026, 1, 1, 7, 0))
    assert not scheduler.in_quiet_hours(datetime(2026, 1, 1, 12, 0))

    scheduler2, _, _, memory2 = make_scheduler(tmp_path, quiet_hours=(1, 8))
    assert scheduler2.in_quiet_hours(datetime(2026, 1, 1, 1, 0))
    assert scheduler2.in_quiet_hours(datetime(2026, 1, 1, 7, 59))
    assert not scheduler2.in_quiet_hours(datetime(2026, 1, 1, 8, 0))
    memory.close()
    memory2.close()


def test_quiet_hours_off(tmp_path: Path) -> None:
    scheduler, _, _, memory = make_scheduler(tmp_path, quiet_hours=None)
    assert not scheduler.in_quiet_hours(datetime(2026, 1, 1, 3, 0))
    memory.close()


class FakeBili:
    async def hot(self, limit: int = 20) -> list[Video]:
        return [
            Video(bvid="BV1", title="一", author="UP"),
            Video(bvid="BV2", title="二", author="UP"),
        ]

    async def ranking(self, limit: int = 20) -> list[Video]:
        return [Video(bvid="BV2", title="二", author="UP"), Video(bvid="BV3", title="三", author="UP")]


async def test_browse_loop_dedupes(tmp_path: Path) -> None:
    memory = Memory(tmp_path / "browse.db").connect()
    config = Config(llm_api_key="x", browse_interval_minutes=40)
    loop = BrowseLoop(config=config, bili=FakeBili(), memory=memory)  # type: ignore[arg-type]

    assert await loop.browse_once() == 3
    assert memory.count_videos() == 3
    assert await loop.browse_once() == 0
    assert memory.count_videos() == 3
    assert memory.get_float("last_browse_at") > 0
    memory.close()
