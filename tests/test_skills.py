from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dafeiyu.agent import Agent
from dafeiyu.bilibili import HistoryItem, Video
from dafeiyu.config import Config
from dafeiyu.learning import LearningLoop
from dafeiyu.llm import LLMResponse
from dafeiyu.frames import FrameReadResult
from dafeiyu.memory import Memory
from dafeiyu.skills import SkillStore

SKILL_JSON = json.dumps(
    {
        "name": "用 asyncio 写并发任务",
        "goal": "把串行 IO 改成并发执行",
        "when_to_use": ["接口批量请求"],
        "tools": [
            {
                "name": "Python 3.10+",
                "kind": "library",
                "install": "brew install python",
                "url": "https://www.python.org/downloads/",
            }
        ],
        "websites": [
            {
                "name": "asyncio 文档",
                "url": "https://docs.python.org/3/library/asyncio.html",
                "purpose": "API 参考",
            }
        ],
        "prerequisites": ["会写 async def"],
        "steps": [
            {
                "title": "创建任务",
                "detail": "用 gather 并发",
                "command": "python -m asyncio",
                "url": "",
            }
        ],
        "verification": ["耗时接近最慢请求"],
        "pitfalls": ["不要阻塞事件循环"],
        "references": [
            {"title": "官方教程", "url": "https://docs.python.org/3/library/asyncio-task.html"}
        ],
        "confidence": "high",
    },
    ensure_ascii=False,
)


class FakeLLM:
    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, *, tools=None, temperature=None, max_tokens=None, source="chat"):
        self.calls.append({"messages": messages, "tools": tools})
        if not self.contents:
            raise AssertionError("FakeLLM 没有更多回复了")
        item = self.contents.pop(0)
        return item if isinstance(item, LLMResponse) else LLMResponse(content=item)


class FakeBili:
    def __init__(self, items: list[HistoryItem]) -> None:
        self.items = items
        self.history_calls = 0

    async def history(self, limit: int = 20) -> list[HistoryItem]:
        self.history_calls += 1
        return self.items[:limit]

    async def watch(
        self,
        video_id: str,
        *,
        comment_limit: int = 6,
        subtitle_chars: int = 1500,
        include_danmaku: bool = False,
    ) -> dict[str, Any]:
        return {
            "video": {
                "bvid": video_id,
                "title": "Python 异步编程入门",
                "标签": ["Python", "异步"],
            },
            "热门评论": [{"text": "讲得清楚"}],
        }

    async def play_url(self, video: Any) -> str:
        return "https://cdn.example/v.mp4"

    async def hot(self, limit: int = 20) -> list:
        return []

    async def ranking(self, limit: int = 20) -> list:
        return []

    async def search(self, keyword: str, limit: int = 10) -> list:
        return [Video(bvid="BV1search01", title=f"{keyword} 视频", author="UP", duration=600)]

    async def video(self, id_or_url: str) -> Video:
        return Video(bvid="BV1manual01", title="手动加入的视频", author="UP", duration=600)

    async def comments(self, video_id: str, limit: int = 8) -> list:
        return []


def make_config(tmp_path: Path, **overrides: Any) -> Config:
    defaults = dict(
        llm_api_key="sk-test",
        owner_qq=1,
        bili_cookie="SESSDATA=test",
        skills_dir=tmp_path / "skills",
        learning_min_watch_seconds=60,
        learning_items_per_run=2,
    )
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def test_skill_store_roundtrip_and_search(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    skill = store.create(
        name="Python 异步编程",
        description="asyncio 入门",
        triggers=["Python", "异步"],
        content="# Python 异步\n\n## 适用场景\n写并发爬虫",
        source="https://www.bilibili.com/video/BV1xx411c7mD",
    )
    assert skill.id == "python-异步编程"
    assert store.count() == 1
    assert store.get(skill.id).content.startswith("# Python 异步")

    matches = store.search("Python 异步 怎么写")
    assert matches and matches[0].id == skill.id
    assert "Python 异步编程" in store.injection_text("Python")

    duplicate = store.create(name="Python 异步编程", content="另一份")
    assert duplicate.id == "python-异步编程-2"

    updated = store.update(skill.id, description="更新后的说明", triggers=["asyncio"])
    assert updated and updated.description == "更新后的说明"
    assert store.search("asyncio")[0].id == skill.id
    assert store.get("../../.env") is None
    assert store.delete(skill.id)
    assert store.get(skill.id) is None


def test_skill_store_rejects_empty_content(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    try:
        store.create(name="空的", content="   ")
    except ValueError:
        pass
    else:
        raise AssertionError("空技能应该报错")


async def test_learning_loop_creates_skill_and_marks_video(tmp_path: Path) -> None:
    item = HistoryItem(
        bvid="BV1xx411c7mD",
        title="Python 异步编程入门",
        author="UP",
        duration=600,
        progress=-1,
    )
    memory = Memory(":memory:").connect()
    store = SkillStore(tmp_path / "skills")
    config = make_config(tmp_path)
    loop = LearningLoop(
        config=config,
        bili=FakeBili([item]),  # type: ignore[arg-type]
        llm=FakeLLM([SKILL_JSON]),  # type: ignore[arg-type]
        memory=memory,
        skills=store,
    )

    result = await loop.run_once()

    assert result.ok and len(result.learned) == 1
    assert store.count() == 1
    assert memory.has_learned(item.bvid)
    skill = store.list()[0]
    assert skill.name == "用 asyncio 写并发任务"
    assert "## 需要的工具" in skill.content
    assert "| Python 3.10+ | library | brew install python |" in skill.content
    assert "## 操作步骤" in skill.content
    assert "python -m asyncio" in skill.content
    assert "https://docs.python.org/3/library/asyncio.html" in skill.content
    assert skill.tools == ("Python 3.10+",)
    assert "https://www.python.org/downloads/" in skill.links
    assert "https://docs.python.org/3/library/asyncio-task.html" in skill.links
    memory.close()


async def test_learning_loop_skips_entertainment_and_short_history(tmp_path: Path) -> None:
    short = HistoryItem(bvid="BVshort", title="随便点开", progress=5)
    funny = HistoryItem(bvid="BVfunny", title="搞笑视频", progress=300)
    memory = Memory(":memory:").connect()
    store = SkillStore(tmp_path / "skills")
    config = make_config(tmp_path)
    loop = LearningLoop(
        config=config,
        bili=FakeBili([short, funny]),  # type: ignore[arg-type]
        llm=FakeLLM(['{"skip": true, "reason": "纯娱乐"}']),  # type: ignore[arg-type]
        memory=memory,
        skills=store,
    )

    result = await loop.run_once()

    assert result.ok and result.skipped == 1
    assert store.count() == 0
    assert memory.has_learned(funny.bvid)
    assert not memory.has_learned(short.bvid)
    memory.close()


async def test_learning_loop_skips_low_information_output(tmp_path: Path) -> None:
    item = HistoryItem(bvid="BVlow", title="很空的视频", progress=300, duration=600)
    memory = Memory(":memory:").connect()
    store = SkillStore(tmp_path / "skills")
    loop = LearningLoop(
        config=make_config(tmp_path),
        bili=FakeBili([item]),  # type: ignore[arg-type]
        llm=FakeLLM(["这不是 JSON，只是一个模糊的总结"]),  # type: ignore[arg-type]
        memory=memory,
        skills=store,
    )
    result = await loop.run_once()
    assert result.ok and result.skipped == 1
    assert store.count() == 0
    assert memory.has_learned(item.bvid)
    memory.close()


async def test_learning_loop_requires_cookie(tmp_path: Path) -> None:
    memory = Memory(":memory:").connect()
    config = make_config(tmp_path, bili_cookie="")
    loop = LearningLoop(
        config=config,
        bili=FakeBili([]),  # type: ignore[arg-type]
        llm=FakeLLM([]),  # type: ignore[arg-type]
        memory=memory,
        skills=SkillStore(tmp_path / "skills"),
    )
    result = await loop.run_once()
    assert not result.ok
    assert "BILI_COOKIE" in result.message
    memory.close()


async def test_agent_injects_private_skills_and_exposes_tools(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    skill = store.create(
        name="Python 异步",
        description="asyncio 笔记",
        triggers=["Python", "异步"],
        content="事件循环、协程、await",
    )
    memory = Memory(tmp_path / "agent.db").connect()
    config = make_config(tmp_path)
    llm = FakeLLM(
        [
            LLMResponse(
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "skill_search", "arguments": '{"query":"异步"}'},
                    }
                ]
            ),
            LLMResponse(content="可以先看事件循环"),
        ]
    )
    agent = Agent(
        config=config,
        llm=llm,  # type: ignore[arg-type]
        bili=FakeBili([]),  # type: ignore[arg-type]
        memory=memory,
        skills=store,
    )

    reply = await agent.respond("private:1", "Python 异步怎么写？")

    assert reply == "可以先看事件循环"
    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert "Python 异步" in system_prompt
    tool_messages = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_messages and skill.id in tool_messages[0]["content"]
    memory.close()


async def test_agent_does_not_inject_skills_in_group_or_allow_tool(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    store.create(name="Python 异步", triggers=["Python"], content="私有笔记")
    memory = Memory(tmp_path / "agent.db").connect()
    config = make_config(tmp_path)
    llm = FakeLLM(
        [
            LLMResponse(
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "skill_search", "arguments": '{"query":"Python"}'},
                    }
                ]
            ),
            LLMResponse(content="群里不说这个"),
        ]
    )
    agent = Agent(
        config=config,
        llm=llm,  # type: ignore[arg-type]
        bili=FakeBili([]),  # type: ignore[arg-type]
        memory=memory,
        skills=store,
    )

    await agent.respond("group:9:1", "Python 怎么学", in_group=True)

    assert "私有笔记" not in llm.calls[0]["messages"][0]["content"]
    tool_messages = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_messages and "只有主人私聊" in tool_messages[0]["content"]
    memory.close()


async def test_learning_manual_url_without_cookie(tmp_path: Path) -> None:
    memory = Memory(":memory:").connect()
    store = SkillStore(tmp_path / "skills")
    loop = LearningLoop(
        config=make_config(tmp_path, bili_cookie=""),
        bili=FakeBili([]),  # type: ignore[arg-type]
        llm=FakeLLM([SKILL_JSON]),  # type: ignore[arg-type]
        memory=memory,
        skills=store,
    )
    result = await loop.learn_url("https://www.bilibili.com/video/BV1manual01")
    assert result.ok and len(result.learned) == 1
    assert store.count() == 1
    assert memory.has_learned("BV1manual01")
    memory.close()


async def test_learning_keywords_without_cookie(tmp_path: Path) -> None:
    memory = Memory(":memory:").connect()
    store = SkillStore(tmp_path / "skills")
    loop = LearningLoop(
        config=make_config(tmp_path, bili_cookie=""),
        bili=FakeBili([]),  # type: ignore[arg-type]
        llm=FakeLLM([SKILL_JSON]),  # type: ignore[arg-type]
        memory=memory,
        skills=store,
    )
    result = await loop.learn_keywords("Python", limit=1)
    assert result.ok and len(result.learned) == 1
    assert store.count() == 1
    memory.close()


async def test_redistill_updates_existing_skill(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    skill = store.create(
        name="旧笔记",
        content="# 旧笔记\n\n只有概念总结",
        source="https://www.bilibili.com/video/BV1manual01",
    )
    memory = Memory(":memory:").connect()
    loop = LearningLoop(
        config=make_config(tmp_path),
        bili=FakeBili([]),  # type: ignore[arg-type]
        llm=FakeLLM([SKILL_JSON]),  # type: ignore[arg-type]
        memory=memory,
        skills=store,
    )

    result = await loop.redistill_all()

    assert result.ok and "更新 1" in result.message
    updated = store.get(skill.id)
    assert updated is not None
    assert updated.name == "用 asyncio 写并发任务"
    assert updated.tools == ("Python 3.10+",)
    assert "## 需要的工具" in updated.content
    assert "## 操作步骤" in updated.content
    memory.close()


async def test_redistill_keeps_old_card_when_skipped(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    original = "# 旧笔记\n\n只有概念总结"
    skill = store.create(
        name="旧笔记",
        content=original,
        source="https://www.bilibili.com/video/BV1manual01",
    )
    memory = Memory(":memory:").connect()
    loop = LearningLoop(
        config=make_config(tmp_path),
        bili=FakeBili([]),  # type: ignore[arg-type]
        llm=FakeLLM(['{"skip": true, "reason": "没有可复用操作"}']),  # type: ignore[arg-type]
        memory=memory,
        skills=store,
    )

    result = await loop.redistill_all()

    assert result.ok and "跳过 1" in result.message
    kept = store.get(skill.id)
    assert kept is not None and kept.content == original
    memory.close()


class FakeFrames:
    available = True

    def __init__(self, text: str = "画面里的代码：print('hello')") -> None:
        self.text = text

    async def read_frames(self, source, timestamps, *, referer=""):
        return FrameReadResult(count=len(timestamps), text=self.text)


async def test_learning_includes_frame_ocr_in_prompt(tmp_path: Path) -> None:
    memory = Memory(":memory:").connect()
    store = SkillStore(tmp_path / "skills")
    llm = FakeLLM([SKILL_JSON])
    loop = LearningLoop(
        config=make_config(tmp_path, bili_cookie=""),
        bili=FakeBili([]),  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        memory=memory,
        skills=store,
        frames=FakeFrames(),  # type: ignore[arg-type]
    )

    result = await loop.learn_url("https://www.bilibili.com/video/BV1manual01")

    assert result.ok and store.count() == 1
    prompt = llm.calls[0]["messages"][1]["content"]
    assert "画面文字" in prompt
    assert "print('hello')" in prompt
    memory.close()
