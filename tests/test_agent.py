from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from dafeiyu.agent import Agent
from dafeiyu.bilibili import Video
from dafeiyu.config import Config
from dafeiyu.llm import LLMResponse
from dafeiyu.memory import Memory


class FakeLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, *, tools=None, temperature=None, max_tokens=None, source="chat"):
        self.calls.append({"messages": messages, "tools": tools, "temperature": temperature})
        if not self.responses:
            raise AssertionError("FakeLLM 没有更多回复了")
        return self.responses.pop(0)


class FakeComputer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, action: str, **params) -> dict:
        self.calls.append((action, params))
        return {"ok": True, "action": action, **params}


class FakeBili:
    def __init__(self) -> None:
        self.hot_calls = 0

    async def hot(self, limit: int = 20) -> list[Video]:
        self.hot_calls += 1
        return [Video(bvid="BV1xx411c7mD", title="热门鱼", author="UP", duration=60, stats={"view": 1})]

    async def ranking(self, limit: int = 20) -> list[Video]:
        return []

    async def search(self, keyword: str, limit: int = 10) -> list[Video]:
        return [Video(bvid="BV1yy411c7mE", title=f"搜到 {keyword}", author="UP2")]

    async def watch(self, video_id: str, *, comment_limit: int = 6) -> dict[str, Any]:
        return {"video": {"bvid": video_id, "title": "看过的视频"}, "热门评论": [{"text": "好看"}]}

    async def comments(self, video_id: str, limit: int = 8) -> list[dict[str, Any]]:
        return [{"user": "甲", "text": "好看", "like": 1}]


def make_agent(
    tmp_path: Path,
    llm: FakeLLM,
    *,
    bili: FakeBili | None = None,
    computer: FakeComputer | None = None,
    **config_overrides: Any,
) -> tuple[Agent, Memory, FakeBili]:
    memory = Memory(tmp_path / "agent.db").connect()
    fake_bili = bili or FakeBili()
    config = Config(
        llm_api_key="sk-test",
        owner_qq=1,
        history_turns=4,
        **config_overrides,
    )
    agent = Agent(
        config=config,
        llm=llm,  # type: ignore[arg-type]
        bili=fake_bili,  # type: ignore[arg-type]
        memory=memory,
        computer=computer,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 11, 3, 14, 30),
    )
    return agent, memory, fake_bili


async def test_respond_with_tool_call(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            LLMResponse(
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bili_hot", "arguments": '{"limit": 2}'},
                    }
                ]
            ),
            LLMResponse(content="大肥鱼：刚刷到一个视频，鱼尾巴都笑抽了"),
        ]
    )
    agent, memory, bili = make_agent(tmp_path, llm)

    reply = await agent.respond("private:1", "看看 B 站热门")

    assert reply == "刚刷到一个视频，鱼尾巴都笑抽了"
    assert bili.hot_calls == 1
    assert [m["role"] for m in memory.recent_messages("private:1", 10)] == ["user", "assistant"]
    tool_messages = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_messages and "热门鱼" in tool_messages[0]["content"]
    assert memory.get_float("last_user_ts:private:1") > 0
    memory.close()


async def test_remember_prefix_and_context(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            LLMResponse(content="记下了，周末考试别摸鱼"),
            LLMResponse(content="考完再摸鱼"),
        ]
    )
    agent, memory, _ = make_agent(tmp_path, llm)

    await agent.respond("private:1", "记住我周末要考试")
    assert "我周末要考试" in memory.list_facts()

    await agent.respond("private:1", "我好慌")
    system_prompt = llm.calls[-1]["messages"][0]["content"]
    assert "我周末要考试" in system_prompt
    memory.close()


async def test_group_prompt_mentions_privacy(tmp_path: Path) -> None:
    llm = FakeLLM([LLMResponse(content="咕噜")])
    agent, memory, _ = make_agent(tmp_path, llm)
    await agent.respond("group:9:1", "在吗", in_group=True)
    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert "QQ 群" in system_prompt
    assert "别透露私聊" in system_prompt
    memory.close()


def test_sanitize(tmp_path: Path) -> None:
    agent, memory, _ = make_agent(tmp_path, FakeLLM([]))
    assert agent.sanitize("大肥鱼：你好呀") == "你好呀"
    assert agent.sanitize("“你好呀”") == "你好呀"
    assert agent.sanitize("**你好**") == "你好"
    assert agent.sanitize("").startswith("咕噜")
    memory.close()


async def test_proactive_share_video(tmp_path: Path) -> None:
    llm = FakeLLM([LLMResponse(content="刚刷到这个，笑死 https://www.bilibili.com/video/BV1xx411c7mD")])
    agent, memory, _ = make_agent(tmp_path, llm)
    memory.upsert_video(
        bvid="BV1xx411c7mD",
        title="鱼的一天",
        author="养鱼人",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        description="一条鱼",
        stats={"view": 10},
    )

    message = await agent.compose_proactive("private:1", "browse")

    assert message is not None
    assert message.bvid == "BV1xx411c7mD"
    assert "BV1xx411c7mD" in message.text
    prompt = llm.calls[0]["messages"][-1]["content"]
    assert "鱼的一天" in prompt
    assert "看过的视频" in prompt
    memory.close()


async def test_proactive_adds_link_when_model_forgets(tmp_path: Path) -> None:
    llm = FakeLLM([LLMResponse(content="这个视频笑死，你一定得看")])
    agent, memory, _ = make_agent(tmp_path, llm)
    memory.upsert_video(
        bvid="BV1xx411c7mD",
        title="鱼的一天",
        author="养鱼人",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
    )
    message = await agent.compose_proactive("private:1", "browse")
    assert message is not None
    assert message.text.endswith("https://www.bilibili.com/video/BV1xx411c7mD")
    memory.close()


async def test_proactive_skip(tmp_path: Path) -> None:
    llm = FakeLLM([LLMResponse(content="[SKIP]"), LLMResponse(content="[SKIP]")])
    agent, memory, _ = make_agent(tmp_path, llm)
    memory.upsert_video(bvid="BV1xx411c7mD", title="鱼的一天", author="养鱼人")
    assert await agent.compose_proactive("private:1", "browse") is None
    memory.close()


async def test_record_outgoing(tmp_path: Path) -> None:
    agent, memory, _ = make_agent(tmp_path, FakeLLM([]))
    agent.record_outgoing("private:1", "主动说一句")
    assert memory.recent_messages("private:1", 5)[0]["content"] == "主动说一句"
    memory.close()


async def test_computer_tool_owner_only(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            LLMResponse(
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "computer", "arguments": '{"action": "status"}'},
                    }
                ]
            ),
            LLMResponse(content="你现在在用 Safari"),
        ]
    )
    computer = FakeComputer()
    agent, memory, _ = make_agent(tmp_path, llm, computer=computer)

    reply = await agent.respond("private:1", "我现在在用什么")
    assert reply == "你现在在用 Safari"
    assert computer.calls == [("status", {})]
    actions = memory.recent_computer_actions()
    assert actions[0]["action"] == "status"
    assert actions[0]["ok"] is True
    memory.close()


async def test_computer_tool_denied_in_group(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            LLMResponse(
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "computer", "arguments": '{"action": "open_app", "app": "Safari"}'},
                    }
                ]
            ),
            LLMResponse(content="这个只能在私聊里做"),
        ]
    )
    computer = FakeComputer()
    agent, memory, _ = make_agent(tmp_path, llm, computer=computer)

    await agent.respond("group:9:1", "帮我打开 Safari")
    assert computer.calls == []
    tool_messages = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert "只有主人私聊" in tool_messages[0]["content"]
    memory.close()


async def test_activity_context_only_in_private(tmp_path: Path) -> None:
    agent, memory, _ = make_agent(tmp_path, FakeLLM([]))
    memory.add_activity("Safari", "一个网页", 2)

    private_prompt = agent._system_prompt(in_group=False)
    assert "Safari" in private_prompt
    assert "一个网页" in private_prompt

    group_prompt = agent._system_prompt(in_group=True)
    assert "Safari" not in group_prompt
    memory.close()


async def test_recent_activity_tool(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            LLMResponse(
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "recent_activity", "arguments": '{"hours": 2}'},
                    }
                ]
            ),
            LLMResponse(content="你刚才在用 Code"),
        ]
    )
    agent, memory, _ = make_agent(tmp_path, llm)
    memory.add_activity("Code", "main.py", 0)

    await agent.respond("private:1", "我刚才在干嘛")
    tool_messages = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert "main.py" in tool_messages[0]["content"]
    memory.close()
