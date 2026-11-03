from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from dafeiyu.agent import ProactiveMessage
from dafeiyu.config import Config
from dafeiyu.memory import Memory
from dafeiyu.onebot import IncomingMessage
from dafeiyu.router import Router, split_message


class FakeBot:
    def __init__(self) -> None:
        self.private: list[tuple[int, str]] = []
        self.groups: list[tuple[int, str, int | None]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def send_private(self, user_id: int, text: str) -> None:
        self.private.append((user_id, text))

    async def send_group(self, group_id: int, text: str, *, at_user: int | None = None) -> None:
        self.groups.append((group_id, text, at_user))

    async def call(self, action: str, params: dict[str, Any], *, timeout: float = 20.0) -> dict[str, Any]:
        self.calls.append((action, params))
        return {"status": "ok", "retcode": 0, "data": {}}


class FakePersonaCard:
    path = Path("/tmp/persona.md")
    source = "file"

    def load(self) -> str:
        return "人格卡内容"


class FakeComputer:
    async def get_status(self) -> dict[str, Any]:
        return {"ok": True, "app": "Safari", "window": "示例网页", "idle_seconds": 125}


class FakeAgent:
    def __init__(self, reply: str = "收到") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []
        self.persona_card = FakePersonaCard()
        self.computer = FakeComputer()

    async def respond(self, session_id: str, text: str, *, in_group: bool = False) -> str:
        self.calls.append({"session_id": session_id, "text": text, "in_group": in_group})
        return self.reply


class FakeBrowse:
    def __init__(self, fresh: int = 2) -> None:
        self.fresh = fresh
        self.called = 0

    async def browse_once(self) -> int:
        self.called += 1
        return self.fresh


class FakeScheduler:
    def __init__(self) -> None:
        self.force_calls: list[bool] = []
        self.postponed = 0

    async def maybe_send(self, *, force: bool = False) -> ProactiveMessage:
        self.force_calls.append(force)
        return ProactiveMessage(text="主动说一句")

    def postpone(self) -> None:
        self.postponed += 1


def make_router(
    tmp_path: Path,
    *,
    scheduler: FakeScheduler | None = None,
    browse: FakeBrowse | None = None,
    **overrides: Any,
) -> tuple[Router, FakeBot, FakeAgent, Memory]:
    memory = Memory(tmp_path / "router.db").connect()
    config = Config(
        llm_api_key="sk-test",
        owner_qq=123456,
        allowed_users=frozenset({777}),
        allowed_groups=frozenset({999}),
        **overrides,
    )
    bot = FakeBot()
    agent = FakeAgent()
    router = Router(
        config=config,
        agent=agent,  # type: ignore[arg-type]
        bot=bot,  # type: ignore[arg-type]
        memory=memory,
        scheduler=scheduler,
        browse=browse,
    )
    return router, bot, agent, memory


def message(
    text: str,
    *,
    user: int = 123456,
    group: int | None = None,
    at: bool = False,
) -> IncomingMessage:
    return IncomingMessage(
        message_type="group" if group else "private",
        user_id=user,
        self_id=999999,
        group_id=group,
        text=text,
        at_me=at,
    )


async def test_owner_private_chat(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle(message("在吗"))
    assert agent.calls == [{"session_id": "private:123456", "text": "在吗", "in_group": False}]
    assert bot.private == [(123456, "收到")]
    memory.close()


async def test_stranger_private_ignored(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle(message("在吗", user=555))
    assert agent.calls == []
    assert bot.private == []
    memory.close()


async def test_allowed_user_private(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle(message("你好", user=777))
    assert len(agent.calls) == 1
    memory.close()


async def test_group_requires_wake(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle(message("随便聊聊", user=777, group=999))
    assert agent.calls == []
    assert bot.groups == []

    await router.handle(message("大肥鱼 看看热门", user=777, group=999))
    assert agent.calls[-1]["text"] == "看看热门"
    assert agent.calls[-1]["in_group"] is True
    assert bot.groups[-1] == (999, "收到", 777)
    memory.close()


async def test_wake_word_must_be_prefix(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle(message("今天大肥鱼好可爱", user=777, group=999))
    assert agent.calls == []
    memory.close()


async def test_group_at_trigger(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle(message("看看热门", user=777, group=999, at=True))
    assert agent.calls[0]["text"] == "看看热门"
    memory.close()


async def test_group_not_allowlisted(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle(message("大肥鱼 在吗", user=777, group=1234, at=True))
    assert agent.calls == []
    memory.close()


async def test_group_at_not_required(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path, group_at_required=False)
    await router.handle(message("随便说说", user=777, group=999))
    assert len(agent.calls) == 1
    memory.close()


async def test_browse_command(tmp_path: Path) -> None:
    browse = FakeBrowse(fresh=4)
    router, bot, agent, memory = make_router(tmp_path, browse=browse)
    await router.handle(message("/刷一下"))
    assert browse.called == 1
    assert "4 条新视频" in bot.private[-1][1]
    assert agent.calls == []
    memory.close()


async def test_force_proactive_command(tmp_path: Path) -> None:
    scheduler = FakeScheduler()
    router, bot, agent, memory = make_router(tmp_path, scheduler=scheduler)
    await router.handle(message("/主动"))
    assert scheduler.force_calls == [True]
    assert "主动说了一句" in bot.private[-1][1]
    memory.close()


async def test_manual_commands_are_owner_only(tmp_path: Path) -> None:
    scheduler = FakeScheduler()
    browse = FakeBrowse()
    router, bot, agent, memory = make_router(tmp_path, scheduler=scheduler, browse=browse)
    await router.handle(message("/主动", user=777))
    await router.handle(message("/刷一下", user=777))
    assert scheduler.force_calls == []
    assert browse.called == 0
    assert all("只有主人" in text for _, text in bot.private)
    memory.close()


async def test_activity_command(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle(message("/在干嘛"))
    assert "Safari" in bot.private[-1][1]
    assert "示例网页" in bot.private[-1][1]
    assert "2 分钟" in bot.private[-1][1]
    memory.close()


async def test_activity_command_owner_only(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle(message("/在干嘛", user=777))
    assert "只有主人" in bot.private[-1][1]
    memory.close()


async def test_persona_command(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle(message("/人格"))
    assert "人格卡" in bot.private[-1][1]
    assert agent.calls == []
    memory.close()


async def test_status_command(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    memory.add_fact("喜欢冰美式")
    await router.handle(message("/状态"))
    assert agent.calls == []
    assert "记得 1 件事" in bot.private[-1][1]
    memory.close()


async def test_quiet_and_wake_commands(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle(message("/安静 30"))
    assert memory.get_float("proactive_paused_until") > time.time()
    assert "30 分钟" in bot.private[-1][1]

    await router.handle(message("/醒醒"))
    assert memory.get_float("proactive_paused_until") == 0
    memory.close()


async def test_commands_are_owner_only(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle(message("/安静", user=777))
    assert memory.get_float("proactive_paused_until") == 0
    assert "只有主人" in bot.private[-1][1]
    memory.close()


async def test_forget_session(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    memory.add_message("private:123456", "user", "旧消息")
    await router.handle(message("/忘掉对话"))
    assert memory.recent_messages("private:123456", 5) == []
    memory.close()


async def test_reply_is_split(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    agent.reply = "一" * 2500
    await router.handle(message("说长点"))
    assert len(bot.private) == 3
    assert all(len(text) <= 1000 for _, text in bot.private)
    memory.close()


def test_split_message_by_lines() -> None:
    text = "\n".join(["a" * 60, "b" * 60, "c" * 60])
    chunks = split_message(text, limit=100)
    assert len(chunks) == 3
    assert all(len(chunk) <= 100 for chunk in chunks)

    long_line = "x" * 250
    chunks = split_message(long_line, limit=100)
    assert len(chunks) == 3
    assert "".join(chunks) == long_line


def test_split_empty() -> None:
    assert split_message("   ") == []


async def test_auto_accept_whitelisted_friend_request(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle_request(
        {"post_type": "request", "request_type": "friend", "user_id": 123456, "flag": "flag-1"}
    )
    assert bot.calls == [("set_friend_add_request", {"flag": "flag-1", "approve": True})]
    memory.close()


async def test_ignore_stranger_friend_request(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path)
    await router.handle_request(
        {"post_type": "request", "request_type": "friend", "user_id": 999999, "flag": "flag-2"}
    )
    assert bot.calls == []
    memory.close()


async def test_auto_accept_can_be_disabled(tmp_path: Path) -> None:
    router, bot, agent, memory = make_router(tmp_path, auto_accept_friends=False)
    await router.handle_request(
        {"post_type": "request", "request_type": "friend", "user_id": 123456, "flag": "flag-3"}
    )
    assert bot.calls == []
    memory.close()
