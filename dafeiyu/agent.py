"""大肥鱼的「大脑」：拼上下文、跑工具、生成回复和主动消息。"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .bilibili import BiliError, BilibiliClient, Video
from .computer import ComputerController, ComputerError
from .config import Config
from .llm import LLMClient, LLMError
from .memory import Memory
from .persona import PersonaCard, build_system_prompt
from .screen import ScreenError, ScreenReader
from .skills import SkillStore
from . import stickers as sticker_catalog

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
MAX_TOOL_RESULT_CHARS = 2500

FACT_PREFIXES = ("帮我记住", "记住", "记一下", "别忘了", "要记得")

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "bili_hot",
            "description": "看看 B 站当前的热门视频（不需要关键词）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "要几条，1-20", "minimum": 1, "maximum": 20}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bili_ranking",
            "description": "看看 B 站全站排行榜。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "要几条，1-20", "minimum": 1, "maximum": 20}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bili_search",
            "description": "在 B 站搜索视频，用于找某个 UP 主、番剧、话题相关的视频。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "要几条，1-10", "minimum": 1, "maximum": 10},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bili_video",
            "description": "看一个视频的详情：标题、UP 主、简介、标签、热评，可能还有字幕节选。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "BV 号、av 号或完整链接"},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bili_comments",
            "description": "看一个视频的热门评论，判断大家在看什么、吵什么。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "BV 号、av 号或完整链接"},
                    "limit": {"type": "integer", "description": "要几条，1-10", "minimum": 1, "maximum": 10},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "把对方明确希望你记住的事写进长期记忆。",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string", "description": "要记住的内容，简短一句话"}},
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "按关键词翻一翻长期记忆。",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "关键词；留空则看最近的记忆"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "computer",
            "description": (
                "在主人的 Mac 上执行安全操作：查看当前窗口、打开 App/网页、搜索、调音量、发通知、"
                "控制音乐、锁屏。只有主人私聊时可用；不支持任意 shell 命令。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "status",
                            "open_app",
                            "open_url",
                            "search_web",
                            "set_volume",
                            "notify",
                            "media",
                            "lock_screen",
                        ],
                    },
                    "app": {"type": "string", "description": "App 名称，如 Safari、微信"},
                    "url": {"type": "string", "description": "http/https 链接"},
                    "query": {"type": "string", "description": "搜索关键词"},
                    "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "音量 0-100"},
                    "title": {"type": "string", "description": "通知标题"},
                    "message": {"type": "string", "description": "通知内容"},
                    "media_action": {
                        "type": "string",
                        "enum": ["play_pause", "next", "previous", "stop"],
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_activity",
            "description": "查看主人电脑最近的活动记录（应用、窗口标题、时间）。只有主人私聊时可用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "minimum": 1, "maximum": 72, "description": "回看多少小时"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_search",
            "description": "检索主人从 B 站学到的本地技能卡。只有主人私聊时可用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "关键词或问题；留空则列出最近的技能"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_read",
            "description": "读取一张技能卡的完整内容。只有主人私聊时可用。",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "技能 ID 或名称"}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sticker_search",
            "description": "按情绪/场景搜索合适的 QQ 表情，返回 [[face:id]] 标记。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "情绪或场景，如 开心、委屈、晚安、学习"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screen_read",
            "description": "读取主人当前屏幕上的文字（本机 OCR）。只有主人私聊且明确让你看屏幕时使用；不要连续调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


@dataclass(slots=True)
class ProactiveMessage:
    text: str
    bvid: str | None = None
    cover: str = ""


class Agent:
    def __init__(
        self,
        *,
        config: Config,
        llm: LLMClient,
        bili: BilibiliClient,
        memory: Memory,
        computer: ComputerController | None = None,
        skills: SkillStore | None = None,
        screen: ScreenReader | None = None,
        clock: Any | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.bili = bili
        self.memory = memory
        self.computer = computer or ComputerController(
            enabled=config.computer_enabled,
            allowed_apps=config.computer_allowed_apps,
        )
        self.skills = skills or SkillStore(config.skills_dir)
        self.screen = screen or ScreenReader(
            enabled=config.screen_read_enabled,
            computer=self.computer,
            cache_dir=config.skills_dir.parent / "bin",
            timeout=config.screen_read_timeout,
            max_chars=config.screen_read_max_chars,
        )
        self.persona_card = PersonaCard(
            config.persona_file,
            name=config.persona_name,
            owner_qq=config.owner_qq,
            max_chars=config.persona_max_chars,
        )
        persona_text = self.persona_card.load()
        log.info(
            "人格卡：%s（%d 字符，%s）",
            self.persona_card.path,
            len(persona_text),
            "文件" if self.persona_card.source == "file" else "内置回退",
        )
        try:
            self.tz = ZoneInfo(config.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            log.warning("时区 %s 用不了，改用系统时区", config.timezone)
            self.tz = None
        self._clock = clock

    # ------------------------------------------------------------------ 对话
    async def respond(self, session_id: str, text: str, *, in_group: bool = False) -> str:
        text = text.strip()
        if not text:
            return ""

        self.memory.add_message(session_id, "user", text)
        now = time.time()
        self.memory.set_state(f"last_user_ts:{session_id}", now)
        self.memory.set_state("last_user_ts", now)
        self._capture_explicit_fact(session_id, text)

        messages = [
            {
                "role": "system",
                "content": self._system_prompt(in_group=in_group, query=text),
            }
        ]
        messages += self._history(session_id)

        response = await self.llm.complete(messages, tools=TOOLS, source="chat")
        for _ in range(MAX_TOOL_ROUNDS):
            if not response.has_tool_calls:
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": response.tool_calls,
                }
            )
            for call in response.tool_calls:
                result = await self._run_tool(call, session_id=session_id)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": result,
                    }
                )
            response = await self.llm.complete(messages, tools=TOOLS, source="chat")
        else:
            if response.has_tool_calls:
                response = await self.llm.complete(messages, tools=None, source="chat")

        reply = self.sanitize(response.content)
        self.memory.add_message(session_id, "assistant", reply)
        return reply

    def record_outgoing(self, session_id: str, text: str) -> None:
        self.memory.add_message(session_id, "assistant", text)

    # ------------------------------------------------------------------ 主动
    async def compose_proactive(self, session_id: str, kind: str = "browse") -> ProactiveMessage | None:
        history = self._history(session_id, limit=8)
        if kind == "browse":
            videos = self.memory.unshared_videos(30)
            random.shuffle(videos)
            for video in videos[:3]:
                message = await self._compose_about_video(video, history)
                if message:
                    return message
        return await self._compose_smalltalk(session_id, history)

    async def _compose_about_video(
        self, video: dict[str, Any], history: list[dict[str, str]]
    ) -> ProactiveMessage | None:
        info: dict[str, Any] = {
            "标题": video.get("title"),
            "UP主": video.get("author"),
            "链接": video.get("url"),
            "时长": video.get("duration"),
            "简介": (video.get("description") or "")[:160],
            "数据": video.get("stats") or {},
        }
        try:
            info["详情"] = await self.bili.watch(video["bvid"], comment_limit=4)
        except Exception as exc:
            log.debug("主动分享前看视频详情失败：%s", exc)
        instruction = (
            "（系统安排：现在轮到你主动找对方说话，对方并没有先开口。不要提“系统”“提示”“自动消息”。）\n"
            "你刚刷 B 站刷到了这条视频：\n"
            f"{json.dumps(info, ensure_ascii=False)}\n"
            "挑你最想聊的点，用 1~2 句话发给对方：说你看到了什么、为什么想给对方看，最后自然地附上链接。\n"
            "不要列清单，不要复述简介，不要编造视频里没给的内容。实在不想说就只回复 [SKIP]。"
        )
        content = await self._plain_completion(history, instruction)
        if not content or "[SKIP]" in content.upper():
            return None
        url = video.get("url") or ""
        if url and url not in content:
            content = f"{content.rstrip()} {url}"
        return ProactiveMessage(text=content, bvid=video["bvid"], cover=video.get("cover") or "")

    async def _compose_smalltalk(
        self, session_id: str, history: list[dict[str, str]]
    ) -> ProactiveMessage | None:
        facts = self.memory.list_facts(limit=12)
        fact_block = "\n".join(f"- {fact}" for fact in facts) if facts else "（暂时没有）"
        instruction = (
            "（系统安排：现在轮到你主动找对方说话，对方并没有先开口。不要提“系统”“提示”“自动消息”。）\n"
            "你刚发完呆，想起对方了。结合你记得的事和最近聊过的话题，用 1~2 句话主动开口：\n"
            "- 说一件你刚刚在做的小事（比如刷到了什么、吃了什么、困了），再问对方一件具体的小事；\n"
            "- 不要空洞地问“在吗”“在干嘛”，不要编造对方没说过的事；\n"
            "- 深夜就催对方睡觉，语气可以损一点。\n"
            f"你记得的事：\n{fact_block}\n"
            "实在没什么想说的就只回复 [SKIP]。"
        )
        content = await self._plain_completion(history, instruction)
        if not content or "[SKIP]" in content.upper():
            return None
        return ProactiveMessage(text=content)

    async def _plain_completion(self, history: list[dict[str, str]], instruction: str) -> str:
        messages = [{"role": "system", "content": self._system_prompt()}, *history]
        messages.append({"role": "user", "content": instruction})
        response = await self.llm.complete(
            messages,
            temperature=min(self.config.llm_temperature + 0.1, 1.2),
            source="proactive",
        )
        return self.sanitize(response.content)

    # ------------------------------------------------------------------ 工具
    async def _run_tool(self, call: dict[str, Any], *, session_id: str) -> str:
        function = call.get("function") or {}
        name = function.get("name") or ""
        raw_args = function.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            if not isinstance(args, dict):
                raise ValueError("参数必须是对象")
        except (ValueError, TypeError) as exc:
            return json.dumps({"error": f"参数解析失败：{exc}"}, ensure_ascii=False)

        try:
            if name == "bili_hot":
                videos = await self.bili.hot(limit=_int(args.get("limit"), 10, 1, 20))
                return _dump([video.to_dict(include_description=False) for video in videos])
            if name == "bili_ranking":
                videos = await self.bili.ranking(limit=_int(args.get("limit"), 10, 1, 20))
                return _dump([video.to_dict(include_description=False) for video in videos])
            if name == "bili_search":
                keyword = str(args.get("keyword") or "").strip()
                if not keyword:
                    return _dump({"error": "关键词不能为空"})
                videos = await self.bili.search(keyword, limit=_int(args.get("limit"), 5, 1, 10))
                if not videos:
                    return _dump({"结果": "没搜到相关视频"})
                return _dump([video.to_dict(include_description=False) for video in videos])
            if name == "bili_video":
                data = await self.bili.watch(str(args.get("id") or ""))
                return _dump(data)
            if name == "bili_comments":
                comments = await self.bili.comments(str(args.get("id") or ""), limit=_int(args.get("limit"), 6, 1, 10))
                return _dump({"comments": comments} if comments else {"结果": "没有拿到评论"})
            if name == "remember":
                content = str(args.get("content") or "").strip()[:300]
                if not content:
                    return _dump({"error": "内容为空，没记"})
                created = self.memory.add_fact(content, source=session_id)
                return _dump({"ok": True, "记住": content, "新的": created})
            if name == "recall":
                keyword = str(args.get("keyword") or "").strip()
                facts = self.memory.search_facts(keyword) if keyword else self.memory.list_facts(limit=20)
                return _dump({"facts": facts} if facts else {"结果": "没想起来相关的事"})
            if name == "computer":
                if not self._owner_only(session_id):
                    return _dump({"error": "只有主人私聊时才能操作电脑"})
                if not self.config.computer_enabled:
                    return _dump({"error": "电脑操作已关闭（COMPUTER_ENABLED=false）"})
                action = str(args.get("action") or "").strip()
                params = {key: value for key, value in args.items() if key != "action"}
                try:
                    result = await self.computer.execute(action, **params)
                except ComputerError as exc:
                    result = {"ok": False, "action": action, "error": str(exc)}
                except Exception as exc:
                    log.exception("电脑操作失败：%s", action)
                    result = {"ok": False, "action": action, "error": f"操作失败：{exc}"}
                self.memory.add_computer_action(action, params, result, ok=bool(result.get("ok")))
                return _dump(result)
            if name == "recent_activity":
                if not self._owner_only(session_id):
                    return _dump({"error": "只有主人私聊时才能查看电脑活动"})
                hours = _int(args.get("hours"), 2, 1, 72)
                events = self.memory.recent_activity(limit=30, since=time.time() - hours * 3600)
                if not events:
                    return _dump({"结果": f"最近 {hours} 小时没有记录到电脑活动"})
                return _dump({"hours": hours, "活动": events})
            if name == "skill_search":
                if not self._owner_only(session_id):
                    return _dump({"error": "只有主人私聊时才能使用技能库"})
                query = str(args.get("query") or "").strip()
                limit = _int(args.get("limit"), 5, 1, 10)
                matches = self.skills.search(query, limit=limit)
                if not matches:
                    return _dump({"结果": "没有找到相关技能"})
                return _dump(
                    {
                        "skills": [
                            {**skill.to_dict(), "summary": skill.summary()} for skill in matches
                        ]
                    }
                )
            if name == "skill_read":
                if not self._owner_only(session_id):
                    return _dump({"error": "只有主人私聊时才能使用技能库"})
                skill = self.skills.get(str(args.get("id") or ""))
                if skill is None:
                    return _dump({"error": "没有找到这个技能"})
                return _dump(
                    {
                        "id": skill.id,
                        "name": skill.name,
                        "description": skill.description,
                        "source": skill.source,
                        "content": skill.content[:4000],
                    }
                )
            if name == "sticker_search":
                query = str(args.get("query") or "").strip()
                matches = sticker_catalog.search(query, limit=6)
                return _dump(
                    {
                        "stickers": [
                            {"id": item.id, "marker": item.marker, "name": item.name, "mood": item.mood}
                            for item in matches
                        ]
                    }
                )
            if name == "screen_read":
                if not self._owner_only(session_id):
                    return _dump({"error": "只有主人私聊时才能读取屏幕"})
                return _dump(await self.screen.read())
        except BiliError as exc:
            return _dump({"error": str(exc)})
        except Exception as exc:  # 工具失败不能把整轮聊天带崩
            log.exception("工具 %s 执行失败", name)
            return _dump({"error": f"工具执行失败：{exc}"})

        return _dump({"error": f"没有这个工具：{name}"})

    def _owner_only(self, session_id: str) -> bool:
        return bool(self.config.owner_qq and session_id == f"private:{self.config.owner_qq}")

    # ------------------------------------------------------------------ 上下文
    def _system_prompt(self, *, in_group: bool = False, query: str | None = None) -> str:
        facts = self.memory.list_facts(limit=30)
        videos = self.memory.recent_videos(limit=5)
        activity = ()
        if not in_group and self.config.activity_enabled:
            activity = self.memory.recent_activity(limit=5)
        skill_text = ""
        if not in_group and query and self.config.skill_inject_limit > 0:
            skill_text = self.skills.injection_text(query, limit=self.config.skill_inject_limit)
        recent_faces = self.memory.top_stickers("face", limit=5)
        sticker_text = (
            "你可以在回复末尾加 1 个 QQ 表情标记，例如 [[face:14]]。"
            "私聊尽量每 1~2 条回复带 1 个，群聊少一点；不要每条都带，最多 2 个。\n"
            "可用表情：\n"
            + sticker_catalog.prompt_catalog(24)
        )
        if recent_faces:
            names = []
            for item in recent_faces:
                face = sticker_catalog.BY_ID.get(str(item["sticker_id"]))
                if face:
                    names.append(f"{face.marker} {face.name}")
            if names:
                sticker_text += "\n主人最近常用的：" + "、".join(names)
        persona_text = self.persona_card.load()
        return build_system_prompt(
            name=self.config.persona_name,
            now=self._now(),
            facts=facts,
            videos=videos,
            activity=activity,
            skill_text=skill_text,
            sticker_text=sticker_text,
            extra=self.config.persona_extra,
            in_group=in_group,
            persona_text=persona_text,
        )

    def _history(self, session_id: str, limit: int | None = None) -> list[dict[str, str]]:
        turns = self.config.history_turns if limit is None else limit
        if turns <= 0:
            return []
        messages = self.memory.recent_messages(session_id, limit=max(turns * 2, 2))
        return [{"role": item["role"], "content": item["content"]} for item in messages]

    def _capture_explicit_fact(self, session_id: str, text: str) -> None:
        for prefix in FACT_PREFIXES:
            if text.startswith(prefix):
                content = text[len(prefix) :].strip(" ：:，,。.!！")
                if len(content) >= 2:
                    self.memory.add_fact(content, source=session_id)
                return

    def _now(self) -> datetime:
        if self._clock is not None:
            return self._clock()
        return datetime.now(self.tz)

    # ------------------------------------------------------------------ 文案
    def sanitize(self, text: str) -> str:
        text = (text or "").strip()
        text = re.sub(r"^(大肥鱼|肥鱼|小鱼)\s*[：:]\s*", "", text)
        pairs = {"“": "”", "‘": "’", "「": "」", "\"": "\"", "'": "'"}
        if len(text) >= 2 and text[-1] == pairs.get(text[0]):
            text = text[1:-1].strip()
        text = text.replace("**", "").strip()
        if not text:
            text = "咕噜咕噜…鱼刚才走神了，你再说一遍？"
        if len(text) > self.config.max_reply_chars:
            text = text[: self.config.max_reply_chars].rstrip() + "…"
        return text


def _dump(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + "…（已截断）"
    return text


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


__all__ = ["Agent", "ProactiveMessage", "TOOLS", "LLMError", "Video"]
