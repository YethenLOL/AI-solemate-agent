from __future__ import annotations

import time
from pathlib import Path

import pytest

from dafeiyu.memory import Memory


@pytest.fixture()
def memory(tmp_path: Path):
    mem = Memory(tmp_path / "test.db").connect()
    yield mem
    mem.close()


def test_messages_roundtrip(memory: Memory) -> None:
    memory.add_message("private:1", "user", "在吗")
    memory.add_message("private:1", "assistant", "在")
    memory.add_message("private:2", "user", "别人")

    messages = memory.recent_messages("private:1", limit=10)
    assert [m["content"] for m in messages] == ["在吗", "在"]
    assert [m["role"] for m in messages] == ["user", "assistant"]

    assert len(memory.recent_messages("private:1", limit=1)) == 1
    assert memory.count_messages("private:1") == 2

    assert memory.clear_session("private:1") == 2
    assert memory.recent_messages("private:1", limit=10) == []


def test_facts_dedup_and_search(memory: Memory) -> None:
    assert memory.add_fact("我周末要考试") is True
    assert memory.add_fact("我周末要考试") is False
    assert memory.add_fact("喜欢喝冰美式") is True

    memories = memory.list_facts()
    assert len(memories) == 2
    assert memory.count_facts() == 2

    assert memory.search_facts("考试") == ["我周末要考试"]
    assert memory.search_facts("不存在") == []


def test_videos_shared_flow(memory: Memory) -> None:
    assert (
        memory.upsert_video(
            bvid="BV1xx411c7mD",
            title="测试视频",
            author="UP",
            url="https://www.bilibili.com/video/BV1xx411c7mD",
            stats={"view": 100},
        )
        is True
    )
    assert memory.upsert_video(bvid="BV1xx411c7mD", title="新标题") is False

    unshared = memory.unshared_videos()
    assert len(unshared) == 1
    assert unshared[0]["title"] == "新标题"
    assert unshared[0]["stats"] == {}

    memory.mark_video_shared("BV1xx411c7mD")
    assert memory.unshared_videos() == []
    assert memory.count_videos() == 1
    assert memory.recent_videos(limit=5)[0]["shared_at"] is not None


def test_state_roundtrip(memory: Memory) -> None:
    memory.set_state("next_proactive_at", 123.5)
    memory.set_state("obj", {"a": 1})
    assert memory.get_float("next_proactive_at") == 123.5
    assert memory.get_state("obj") == {"a": 1}
    assert memory.get_state("missing", "默认") == "默认"
    assert memory.get_int("missing", 7) == 7

    memory.delete_state("obj")
    assert memory.get_state("obj") is None


def test_activity_and_audit(memory: Memory) -> None:
    memory.add_activity("Safari", "网页", 3)
    memory.add_activity("Code", "main.py", 0)
    events = memory.recent_activity(10)
    assert [item["app"] for item in events] == ["Safari", "Code"]
    assert memory.count_activity() == 2

    old = time.time() - 10 * 86400
    memory.add_activity("Old", "旧记录", 0, created_at=old)
    assert memory.prune_activity(7) == 1
    assert memory.count_activity() == 2

    memory.add_computer_action("open_app", {"app": "Safari"}, {"ok": True}, ok=True)
    actions = memory.recent_computer_actions()
    assert actions[0]["action"] == "open_app"
    assert actions[0]["params"] == {"app": "Safari"}
    assert actions[0]["ok"] is True


def test_token_usage_recording(memory: Memory) -> None:
    now = time.time()
    memory.record_usage(
        model="deepseek-chat", source="chat", prompt_tokens=10, completion_tokens=5, created_at=now
    )
    memory.record_usage(
        model="dsh",
        source="learning",
        prompt_tokens=7,
        completion_tokens=3,
        estimated=True,
        created_at=now - 60,
    )

    assert memory.count_usage() == 2
    rows = memory.usage_rows(0)
    assert [row["source"] for row in rows] == ["learning", "chat"]
    assert rows[0]["estimated"] == 1
    assert rows[0]["total_tokens"] == 10
    assert rows[1]["total_tokens"] == 15
    assert memory.usage_rows(now + 1) == []


def test_token_usage_ignores_negative_values(memory: Memory) -> None:
    memory.record_usage(prompt_tokens=-5, completion_tokens=-1)
    rows = memory.usage_rows(0)
    assert rows[0]["prompt_tokens"] == 0
    assert rows[0]["completion_tokens"] == 0
    assert rows[0]["total_tokens"] == 0
