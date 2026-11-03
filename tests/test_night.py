from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dafeiyu.bilibili import DanmakuSummary, Video
from dafeiyu.config import Config
from dafeiyu.diary import DiaryStore
from dafeiyu.frames import FrameReadResult
from dafeiyu.llm import LLMResponse
from dafeiyu.memory import Memory
from dafeiyu.night import NightLoop
from dafeiyu.skills import SkillStore

DIARY_JSON = json.dumps(
    {"letter": "今天看到一条很扎实的教程，想起你也在学这个。", "private": "有点困，但还是想多看点。"},
    ensure_ascii=False,
)


class FakeBili:
    def __init__(self, videos: list[Video], counts: dict[str, int] | None = None) -> None:
        self.videos = videos
        self.counts = counts or {}

    async def hot(self, limit: int = 20) -> list[Video]:
        return self.videos[:limit]

    async def ranking(self, limit: int = 20) -> list[Video]:
        return []

    async def danmaku(self, video: Video) -> DanmakuSummary:
        total = self.counts.get(video.bvid, 0)
        return DanmakuSummary(
            total=total,
            duration=video.duration,
            per_minute=[total],
            hot=[{"开始秒": 60, "结束秒": 120, "弹幕数": total, "代表弹幕": ["来了"]}],
        )

    async def play_url(self, video: Video) -> str:
        return f"https://cdn.example/{video.bvid}.mp4"

    async def watch(self, bvid: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "video": {"bvid": bvid, "title": "示例", "标签": ["Python"]},
            "热门评论": [{"text": "讲得清楚"}],
            "字幕节选": "先安装依赖，再运行命令。",
        }


class FakeLLM:
    def __init__(self, score: int = 16) -> None:
        self.score = score
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(self, messages, *, tools=None, temperature=None, max_tokens=None, source="chat"):
        self.calls.append(messages)
        system = messages[0]["content"]
        if "内容筛选器" in system:
            content = json.dumps(
                {
                    "score": self.score,
                    "reason": "有具体工具和操作步骤，和主人最近在学的东西相关",
                    "message": "这条把环境配置讲透了，值得看。",
                },
                ensure_ascii=False,
            )
        else:
            content = DIARY_JSON
        return LLMResponse(content=content)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_private(self, user_id: int, text: str) -> None:
        self.sent.append((user_id, text))


def make_config(tmp_path: Path, **overrides: Any) -> Config:
    defaults = dict(
        llm_api_key="sk-test",
        owner_qq=1,
        skills_dir=tmp_path / "skills",
        diary_dir=tmp_path / "diary",
        db_path=tmp_path / "test.db",
        night_enabled=True,
        night_initial_delay=0,
        night_max_videos=5,
        night_deep_read=2,
        share_enabled=True,
        share_min_score=13,
        diary_push=True,
        proactive_enabled=False,
    )
    defaults.update(overrides)
    return Config(**defaults)


class FakeFrames:
    available = True

    def __init__(self, text: str = "画面里的板书：先装依赖") -> None:
        self.text = text
        self.calls: list[tuple[str, list[float]]] = []

    async def read_frames(self, source, timestamps, *, referer=""):
        self.calls.append((str(source), list(timestamps)))
        return FrameReadResult(count=len(timestamps), text=self.text)


def make_loop(
    tmp_path: Path,
    bili: FakeBili,
    llm: FakeLLM,
    bot: FakeBot,
    memory: Memory,
    frames=None,
) -> NightLoop:
    config = make_config(tmp_path)
    store = SkillStore(config.skills_dir)
    store.create(
        name="Python 异步",
        triggers=["Python", "异步", "asyncio"],
        content="事件循环",
    )
    return NightLoop(
        config=config,
        bili=bili,  # type: ignore[arg-type]
        agent=None,  # type: ignore[arg-type]
        bot=bot,  # type: ignore[arg-type]
        memory=memory,
        llm=llm,  # type: ignore[arg-type]
        skills=store,
        diary=DiaryStore(config.diary_dir),
        frames=frames,
    )


async def test_rank_prefers_preference_hits(tmp_path: Path) -> None:
    memory = Memory(":memory:").connect()
    bili = FakeBili(
        [
            Video(bvid="BVmatch01", title="Python 异步入门", duration=600),
            Video(bvid="BVhot0001", title="搞笑合集", duration=600),
        ],
        counts={"BVhot0001": 3000},
    )
    loop = make_loop(tmp_path, bili, FakeLLM(), FakeBot(), memory)
    ranked = await loop.rank_videos(bili.videos)
    assert ranked[0]["video"].bvid == "BVmatch01"
    assert ranked[0]["hits"] >= 1
    memory.close()


async def test_skip_plan_still_writes_diaries(tmp_path: Path) -> None:
    memory = Memory(tmp_path / "test.db").connect()
    bot = FakeBot()
    loop = make_loop(tmp_path, FakeBili([]), FakeLLM(), bot, memory)

    result = await loop.run_round({"action": "skip"})

    assert result.action == "skip"
    owner = (tmp_path / "diary" / "owner.md").read_text(encoding="utf-8")
    private = (tmp_path / "diary" / "private.md").read_text(encoding="utf-8")
    assert "给主人的信" in owner and "扎实的教程" in owner
    assert "私人日记" in private and "有点困" in private
    assert bot.sent and bot.sent[0][0] == 1
    memory.close()


async def test_round_shares_top_video_then_dedupes(tmp_path: Path) -> None:
    memory = Memory(tmp_path / "test.db").connect()
    bot = FakeBot()
    bili = FakeBili([Video(bvid="BVshare01", title="Python 异步实战", duration=600)], counts={"BVshare01": 200})
    loop = make_loop(tmp_path, bili, FakeLLM(score=17), bot, memory)

    first = await loop.run_round({"action": "wake"})
    assert first.shared_bvid == "BVshare01"
    assert first.share_score == 17
    assert any("BVshare01" in text for _, text in bot.sent)
    assert memory.share_count_since(0) == 1

    second = await loop.run_round({"action": "wake"})
    assert second.shared_bvid == ""
    assert memory.share_count_since(0) == 1
    memory.close()


async def test_low_score_is_not_shared(tmp_path: Path) -> None:
    memory = Memory(tmp_path / "test.db").connect()
    bot = FakeBot()
    bili = FakeBili([Video(bvid="BVlow0001", title="随便看看", duration=60)], counts={"BVlow0001": 1})
    loop = make_loop(tmp_path, bili, FakeLLM(score=5), bot, memory)

    result = await loop.run_round({"action": "wake"})

    assert result.shared_bvid == ""
    assert not any("BVlow0001" in text for _, text in bot.sent)
    memory.close()


async def test_deep_read_includes_frame_ocr(tmp_path: Path) -> None:
    memory = Memory(tmp_path / "test.db").connect()
    bili = FakeBili([Video(bvid="BVframe01", title="Python 教程", duration=600)], counts={"BVframe01": 50})
    frames = FakeFrames()
    loop = make_loop(tmp_path, bili, FakeLLM(), FakeBot(), memory, frames=frames)

    ranked = await loop.rank_videos(bili.videos)
    deep = await loop.deep_read(ranked[0])

    assert deep["details"]["画面文字"] == "画面里的板书：先装依赖"
    assert frames.calls and frames.calls[0][0] == "https://cdn.example/BVframe01.mp4"
    assert frames.calls[0][1] == [65.0]  # 弹幕热点 60s 开始后 5 秒
    memory.close()
