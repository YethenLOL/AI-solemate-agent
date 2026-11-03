"""启动入口：装配组件、处理退出、提供自检命令。"""

from __future__ import annotations

import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .activity import ActivityMonitor
from .agent import Agent
from .bilibili import BilibiliClient
from .computer import ComputerController
from .config import APP_HOME, Config, ConfigError
from .dsh import DSHClient
from .learning import LearningLoop
from .llm import LLMClient
from .memory import Memory
from .onebot import OneBotClient
from .persona import PersonaCard, build_system_prompt
from .router import Router
from .scheduler import BrowseLoop, ProactiveScheduler
from .screen import ScreenReader
from .skills import SkillStore

log = logging.getLogger(__name__)


def add_file_logging(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
                "%Y-%m-%d %H:%M:%S",
            )
        )
        logging.getLogger().addHandler(handler)
    except OSError as exc:
        logging.getLogger(__name__).warning("无法写日志文件 %s：%s", path, exc)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    add_file_logging(APP_HOME / "logs" / "dafeiyu.log")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def build_components(config: Config) -> dict[str, object]:
    memory = Memory(config.db_path).connect()

    def usage_sink(payload: dict[str, Any]) -> None:
        memory.record_usage(**payload)

    http_client = httpx.AsyncClient(
        timeout=20.0,
        headers={
            "User-Agent": config.bili_user_agent,
            "Referer": "https://www.bilibili.com/",
        },
    )
    bili = BilibiliClient(
        cookie=config.bili_cookie,
        user_agent=config.bili_user_agent,
        min_interval=config.bili_min_interval,
        http_client=http_client,
    )
    llm = LLMClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
        timeout=config.llm_timeout,
        usage_sink=usage_sink,
    )
    computer = ComputerController(
        enabled=config.computer_enabled,
        allowed_apps=config.computer_allowed_apps,
    )
    skills = SkillStore(config.skills_dir)
    dsh_client = (
        DSHClient(
            node=config.dsh_node,
            entry=config.dsh_entry,
            profile=config.dsh_profile,
            api_key=config.llm_api_key,
            timeout=config.dsh_timeout,
            workdir=config.skills_dir.parent / "dsh-workspace",
            usage_sink=usage_sink,
        )
        if (config.dsh_enabled or config.dsh_chat)
        else None
    )
    learning_llm = dsh_client if (config.dsh_enabled and dsh_client) else llm
    chat_llm = dsh_client if (config.dsh_chat and dsh_client) else llm
    screen = ScreenReader(
        enabled=config.screen_read_enabled,
        computer=computer,
        cache_dir=config.skills_dir.parent / "bin",
        timeout=config.screen_read_timeout,
        max_chars=config.screen_read_max_chars,
    )
    agent = Agent(
        config=config,
        llm=chat_llm,
        bili=bili,
        memory=memory,
        computer=computer,
        skills=skills,
        screen=screen,
    )
    activity = ActivityMonitor(config=config, memory=memory, computer=computer)
    bot = OneBotClient(url=config.onebot_ws_url, token=config.onebot_token)
    scheduler = ProactiveScheduler(config=config, agent=agent, bot=bot, memory=memory)
    browse = BrowseLoop(config=config, bili=bili, memory=memory)
    learning = LearningLoop(
        config=config, bili=bili, llm=learning_llm, memory=memory, skills=skills
    )
    router = Router(
        config=config,
        agent=agent,
        bot=bot,
        memory=memory,
        scheduler=scheduler,
        browse=browse,
    )
    bot.on_message = router.handle
    bot.on_request = router.handle_request
    return {
        "memory": memory,
        "http": http_client,
        "bili": bili,
        "llm": llm,
        "chat_llm": chat_llm,
        "learning_llm": learning_llm,
        "computer": computer,
        "agent": agent,
        "skills": skills,
        "learning": learning,
        "activity": activity,
        "bot": bot,
        "scheduler": scheduler,
        "router": router,
        "browse": browse,
    }


async def run_app(config: Config, *, stop_event: asyncio.Event | None = None) -> None:
    parts = build_components(config)
    memory: Memory = parts["memory"]  # type: ignore[assignment]
    http_client: httpx.AsyncClient = parts["http"]  # type: ignore[assignment]
    bili: BilibiliClient = parts["bili"]  # type: ignore[assignment]
    bot: OneBotClient = parts["bot"]  # type: ignore[assignment]
    browse: BrowseLoop = parts["browse"]  # type: ignore[assignment]
    scheduler: ProactiveScheduler = parts["scheduler"]  # type: ignore[assignment]
    learning: LearningLoop = parts["learning"]  # type: ignore[assignment]
    learning_llm = parts["learning_llm"]
    chat_llm = parts["chat_llm"]
    activity: ActivityMonitor = parts["activity"]  # type: ignore[assignment]
    skills: SkillStore = parts["skills"]  # type: ignore[assignment]

    log.info("大肥鱼启动：模型 %s，主人 %s", config.llm_model, config.owner_qq or "未设置")
    log.info(
        "电脑感知：%s；主动聊天：%s，每 %d~%d 分钟一次，安静时段 %s",
        "开" if config.activity_enabled else "关",
        "开" if config.proactive_enabled else "关",
        config.proactive_min_minutes,
        config.proactive_max_minutes,
        config.quiet_hours or "无",
    )
    log.info(
        "B 站学习：%s，每 %d 分钟一次；本地技能 %d 个",
        "开" if config.learning_enabled else "关",
        config.learning_interval_minutes,
        skills.count(),
    )
    if dsh_client := (learning_llm if isinstance(learning_llm, DSHClient) else None):
        log.info("DSH 学习运行时：%s", dsh_client.status())
    if config.dsh_chat and isinstance(chat_llm, DSHClient):
        log.info("DSH 聊天大脑：%s（原生工具已禁用，使用本地 JSON 工具协议）", chat_llm.status())

    stop = stop_event or asyncio.Event()
    if stop_event is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, AttributeError, RuntimeError):
                pass

    tasks = [
        asyncio.create_task(bot.run(), name="onebot"),
        asyncio.create_task(browse.run(), name="browse"),
        asyncio.create_task(scheduler.run(), name="proactive"),
        asyncio.create_task(activity.run(), name="activity"),
        asyncio.create_task(learning.run(), name="learning"),
    ]
    try:
        await stop.wait()
    finally:
        log.info("正在退出…")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bot.aclose()
        await bili.aclose()
        await http_client.aclose()
        memory.close()
        log.info("已退出")


async def check_services(config: Config) -> bool:
    """不连 QQ，只检查模型和 B 站接口通不通。"""
    ok = True
    memory = Memory(config.db_path).connect()

    def usage_sink(payload: dict[str, Any]) -> None:
        memory.record_usage(**{**payload, "source": "check"})

    llm = LLMClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        max_tokens=32,
        usage_sink=usage_sink,
    )
    try:
        response = await llm.complete(
            [{"role": "user", "content": "只回复两个字：咕噜"}], source="check"
        )
        print(f"✅ 模型（{config.llm_model}）：{response.content or '(空回复)'}")
    except Exception as exc:
        ok = False
        print(f"❌ 模型（{config.llm_model}）：{exc}")
    finally:
        await llm.aclose()

    if config.dsh_enabled or config.dsh_chat:
        dsh = DSHClient(
            node=config.dsh_node,
            entry=config.dsh_entry,
            profile=config.dsh_profile,
            api_key=config.llm_api_key,
            timeout=config.dsh_timeout,
            workdir=config.skills_dir.parent / "dsh-workspace",
            usage_sink=usage_sink,
        )
        try:
            response = await dsh.complete(
                [{"role": "user", "content": "只回复两个字：咕噜"}], source="check"
            )
            print(f"✅ DSH（{config.dsh_profile}）：{response.content or '(空回复)'}")
        except Exception as exc:
            ok = False
            print(f"❌ DSH：{exc}（{dsh.status()}）")

    bili = BilibiliClient(cookie=config.bili_cookie, user_agent=config.bili_user_agent)
    try:
        videos = await bili.hot(limit=3)
        titles = "；".join(video.title for video in videos) or "（没有数据）"
        print(f"✅ B 站热门：{titles}")
    except Exception as exc:
        ok = False
        print(f"❌ B 站热门：{exc}")
    finally:
        await bili.aclose()

    memory.close()
    return ok


async def test_qq(config: Config) -> bool:
    if not config.owner_qq:
        print("❌ 先在 .env 里填 OWNER_QQ（你自己的 QQ 号）")
        return False

    bot = OneBotClient(url=config.onebot_ws_url, token=config.onebot_token)
    task = asyncio.create_task(bot.run())
    try:
        await bot.wait_connected(timeout=20)
        info = await bot.call("get_login_info")
        data = info.get("data") or {}
        print(f"✅ 已连上 OneBot，机器人 QQ：{data.get('nickname', '?')}（{data.get('user_id', '?')}）")
        await bot.send_private(config.owner_qq, "咕噜咕噜，大肥鱼连上了，能收到吗？")
        print(f"✅ 已给 {config.owner_qq} 发了测试消息，去 QQ 看看")
        return True
    except Exception as exc:
        print(f"❌ QQ 连接失败：{exc}")
        print("   检查 NapCat 是否登录、WebSocket 服务器是否开启、端口和 token 是否对得上")
        return False
    finally:
        await bot.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await bot.aclose()


async def _learning_action(config: Config, action) -> bool:
    memory = Memory(config.db_path).connect()

    def usage_sink(payload: dict[str, Any]) -> None:
        memory.record_usage(**payload)

    http_client = httpx.AsyncClient(timeout=30.0)
    bili = BilibiliClient(
        cookie=config.bili_cookie,
        user_agent=config.bili_user_agent,
        min_interval=config.bili_min_interval,
        http_client=http_client,
    )
    llm = LLMClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
        timeout=config.llm_timeout,
        usage_sink=usage_sink,
    )
    learning_llm = (
        DSHClient(
            node=config.dsh_node,
            entry=config.dsh_entry,
            profile=config.dsh_profile,
            api_key=config.llm_api_key,
            timeout=config.dsh_timeout,
            workdir=config.skills_dir.parent / "dsh-workspace",
            usage_sink=usage_sink,
        )
        if config.dsh_enabled
        else llm
    )
    loop = LearningLoop(
        config=config,
        bili=bili,
        llm=learning_llm,
        memory=memory,
        skills=SkillStore(config.skills_dir),
    )
    try:
        result = await action(loop)
        print(("✅ " if result.ok else "❌ ") + result.message)
        for skill in result.learned:
            print(f"  + [{skill.id}] {skill.name}")
        return result.ok
    finally:
        await llm.aclose()
        await bili.aclose()
        await http_client.aclose()
        memory.close()


async def learn_now(config: Config, limit: int | None = None) -> bool:
    return await _learning_action(config, lambda loop: loop.run_once(limit=limit))


async def learn_url_now(config: Config, url: str) -> bool:
    return await _learning_action(config, lambda loop: loop.learn_url(url))


async def learn_keywords_now(config: Config, query: str, limit: int | None = None) -> bool:
    queries = [part.strip() for part in query.replace("，", ",").split(",") if part.strip()]
    if len(queries) <= 1:
        return await _learning_action(config, lambda loop: loop.learn_keywords(query, limit=limit))
    results = []
    for keyword in queries:
        log.info("关键词学习：%s", keyword)
        results.append(
            await _learning_action(config, lambda loop, kw=keyword: loop.learn_keywords(kw, limit=limit))
        )
    return all(results)


async def relearn_skills_now(config: Config, limit: int | None = None) -> bool:
    return await _learning_action(config, lambda loop: loop.redistill_all(limit=limit or 20))


async def show_history(config: Config, limit: int = 10) -> bool:
    bili = BilibiliClient(cookie=config.bili_cookie, user_agent=config.bili_user_agent)
    try:
        items = await bili.history(limit=limit)
        if not items:
            print("没有读到观看历史（检查 BILI_COOKIE 是否包含 SESSDATA）")
            return False
        for item in items:
            print(f"{item.bvid}  {item.progress:>5}s  {item.title}")
        return True
    except Exception as exc:
        print(f"❌ 读取观看历史失败：{exc}")
        return False
    finally:
        await bili.aclose()


def list_skills(config: Config) -> bool:
    store = SkillStore(config.skills_dir)
    skills = store.list()
    print(f"技能目录：{store.directory}")
    print(f"共 {len(skills)} 个技能")
    for skill in skills:
        print(f"  - {skill.summary()}")
    return True


def search_skills(config: Config, query: str) -> bool:
    store = SkillStore(config.skills_dir)
    matches = store.search(query, limit=5)
    if not matches:
        print(f"没有找到和「{query}」相关的技能")
        return False
    for skill in matches:
        print(f"=== {skill.summary()} ===")
        print(skill.content[:1200])
        print()
    return True


def persona_preview(config: Config) -> None:
    try:
        now = datetime.now(ZoneInfo(config.timezone))
    except (ZoneInfoNotFoundError, ValueError):
        now = datetime.now()
    card = PersonaCard(
        config.persona_file,
        name=config.persona_name,
        owner_qq=config.owner_qq,
        max_chars=config.persona_max_chars,
    )
    text = card.load()
    prompt = build_system_prompt(
        name=config.persona_name,
        now=now,
        extra=config.persona_extra,
        persona_text=text,
    )
    print(f"人格卡：{card.path}")
    print(f"来源：{'文件' if card.source == 'file' else '内置回退'}，{len(text)} 字符（上限 {card.max_chars}）")
    print(f"完整系统提示词：{len(prompt)} 字符，约 {int(len(prompt) / 1.5)} tokens（粗估）")
    print("分节：")
    for heading, count in card.sections():
        print(f"  - {heading}：{count} 字符")
    print("\n===== 完整系统提示词 =====\n")
    print(prompt)


async def persona_test(config: Config, message: str) -> bool:
    memory = Memory(":memory:").connect()
    usage_memory = Memory(config.db_path).connect()

    def usage_sink(payload: dict[str, Any]) -> None:
        usage_memory.record_usage(**{**payload, "source": "persona"})

    http_client = httpx.AsyncClient(
        timeout=30.0,
        headers={
            "User-Agent": config.bili_user_agent,
            "Referer": "https://www.bilibili.com/",
        },
    )
    bili = BilibiliClient(
        cookie=config.bili_cookie,
        user_agent=config.bili_user_agent,
        min_interval=config.bili_min_interval,
        http_client=http_client,
    )
    llm = LLMClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
        timeout=config.llm_timeout,
        usage_sink=usage_sink,
    )
    chat_llm = (
        DSHClient(
            node=config.dsh_node,
            entry=config.dsh_entry,
            profile=config.dsh_profile,
            api_key=config.llm_api_key,
            timeout=config.dsh_timeout,
            workdir=config.skills_dir.parent / "dsh-workspace",
            usage_sink=usage_sink,
        )
        if config.dsh_chat
        else llm
    )
    agent = Agent(
        config=config,
        llm=chat_llm,
        bili=bili,
        memory=memory,
        skills=SkillStore(config.skills_dir),
        screen=ScreenReader(
            enabled=config.screen_read_enabled,
            computer=ComputerController(
                enabled=config.computer_enabled,
                allowed_apps=config.computer_allowed_apps,
            ),
            cache_dir=config.skills_dir.parent / "bin",
            timeout=config.screen_read_timeout,
            max_chars=config.screen_read_max_chars,
        ),
    )
    try:
        text = agent.persona_card.load()
        print(f"人格卡：{agent.persona_card.path}（{len(text)} 字符）")
        session_id = f"private:{config.owner_qq}" if config.owner_qq else "persona-test"
        reply = await agent.respond(session_id, message)
        print(f"\n你：{message}\n鱼：{reply}")
        return True
    except Exception as exc:
        print(f"❌ 人格试聊失败：{exc}")
        return False
    finally:
        await llm.aclose()
        await bili.aclose()
        await http_client.aclose()
        memory.close()
        usage_memory.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="大肥鱼 QQ 机器人")
    parser.add_argument("--check", action="store_true", help="检查模型和 B 站接口")
    parser.add_argument("--test-qq", action="store_true", help="连 NapCat 并发一条测试私聊")
    parser.add_argument("--persona-preview", action="store_true", help="打印当前人格卡和完整系统提示词")
    parser.add_argument("--persona-test", metavar="MESSAGE", help="用当前人格直接和模型试聊一句")
    parser.add_argument("--learn-now", nargs="?", const=2, type=int, metavar="N", help="立即从 B 站历史学习 N 条")
    parser.add_argument("--learn-url", metavar="URL", help="手动学习一个 B 站视频链接")
    parser.add_argument("--learn-search", metavar="KEYWORD", help="按关键词搜索 B 站并学习")
    parser.add_argument("--skills", action="store_true", help="列出自学到的技能")
    parser.add_argument(
        "--relearn-skills", action="store_true", help="把已有技能卡按蒸馏格式重做一遍"
    )
    parser.add_argument("--skill-search", metavar="QUERY", help="搜索本地技能卡")
    parser.add_argument("--bili-history", nargs="?", const=10, type=int, metavar="N", help="查看 B 站观看历史")
    args = parser.parse_args(argv)

    try:
        config = Config.from_env(require_llm=False)
    except ConfigError as exc:
        print(f"配置有问题：{exc}")
        return 2

    no_llm_commands = (
        args.persona_preview
        or args.skills
        or args.skill_search
        or args.bili_history
        or (args.learn_now and config.dsh_enabled)
        or (args.learn_url and config.dsh_enabled)
        or (args.learn_search and config.dsh_enabled)
    )
    if not config.llm_api_key and not no_llm_commands:
        print("配置有问题：缺少 LLM_API_KEY（可先用 --skills / --bili-history 等只读命令）")
        return 2

    setup_logging(config.log_level)

    if args.persona_preview:
        persona_preview(config)
    if args.persona_test:
        return 0 if asyncio.run(persona_test(config, args.persona_test)) else 1
    if args.relearn_skills:
        return 0 if asyncio.run(relearn_skills_now(config)) else 1
    if args.skills:
        return 0 if list_skills(config) else 1
    if args.skill_search:
        return 0 if search_skills(config, args.skill_search) else 1
    if args.bili_history:
        return 0 if asyncio.run(show_history(config, limit=args.bili_history)) else 1
    if args.learn_now:
        return 0 if asyncio.run(learn_now(config, limit=args.learn_now)) else 1
    if args.learn_url:
        return 0 if asyncio.run(learn_url_now(config, args.learn_url)) else 1
    if args.learn_search:
        return 0 if asyncio.run(learn_keywords_now(config, args.learn_search)) else 1
    if args.persona_preview:
        return 0

    if args.check or args.test_qq:
        async def run_checks() -> bool:
            ok = True
            if args.check:
                ok = await check_services(config) and ok
            if args.test_qq:
                ok = await test_qq(config) and ok
            return ok

        return 0 if asyncio.run(run_checks()) else 1

    try:
        asyncio.run(run_app(config))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
