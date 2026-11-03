"""环境变量配置。所有可调项都从 .env / 系统环境变量读取。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_WAKE_WORDS = ("大肥鱼", "小鱼", "肥鱼")
DEFAULT_PERSONA_NAME = "大肥鱼"
DEFAULT_ONEBOT_WS_URL = "ws://127.0.0.1:3001"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_HISTORY_TURNS = 16
DEFAULT_MAX_REPLY_CHARS = 1200
DEFAULT_QUIET_HOURS = (1, 8)
DEFAULT_ACTIVITY_BLOCKLIST = (
    "1Password",
    "Keychain Access",
    "Bitwarden",
    "Passwords",
    "loginwindow",
    "ScreenSaver",
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_HOME = Path(os.environ.get("DAFEIYU_HOME", str(PROJECT_ROOT))).expanduser()

_TRUTHY = {"1", "true", "yes", "y", "on", "是"}


class ConfigError(RuntimeError):
    """配置不合法时抛出，带可直接照做的修复提示。"""


def _get(env: Mapping[str, str], key: str, default: str | None = None) -> str | None:
    raw = env.get(key)
    if raw is None:
        return default
    raw = raw.strip()
    return raw or default


def _as_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = _get(env, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{key} 需要整数，现在是：{raw!r}") from None


def _as_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = _get(env, key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{key} 需要数字，现在是：{raw!r}") from None


def _as_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _get(env, key)
    if raw is None:
        return default
    return raw.lower() in _TRUTHY


def _as_int_set(env: Mapping[str, str], key: str) -> frozenset[int]:
    raw = _get(env, key)
    if not raw:
        return frozenset()
    values: set[int] = set()
    for chunk in raw.replace("，", ",").replace(";", ",").replace(" ", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            values.add(int(chunk))
        except ValueError:
            raise ConfigError(f"{key} 里 {chunk!r} 不是 QQ 号") from None
    return frozenset(values)


def _as_words(env: Mapping[str, str], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _get(env, key)
    if not raw:
        return default
    words = tuple(w.strip() for w in raw.replace("，", ",").split(",") if w.strip())
    return words or default


def _as_quiet_hours(
    env: Mapping[str, str], key: str, default: tuple[int, int] | None
) -> tuple[int, int] | None:
    raw = _get(env, key)
    if raw is None:
        return default
    if raw.lower() in {"off", "none", "no", "关"}:
        return None
    parts = raw.replace("：", ":").split("-")
    if len(parts) != 2:
        raise ConfigError(f"{key} 格式应为 1-8 或 23-7，现在是：{raw!r}")
    try:
        start, end = int(parts[0]), int(parts[1])
    except ValueError:
        raise ConfigError(f"{key} 格式应为 1-8 或 23-7，现在是：{raw!r}") from None
    if not (0 <= start <= 23 and 0 <= end <= 24):
        raise ConfigError(f"{key} 的小时必须在 0~24 之间，现在是：{raw!r}")
    return (start, end)


@dataclass(slots=True)
class Config:
    # LLM（OpenAI 兼容接口，默认 DeepSeek）
    llm_base_url: str = DEFAULT_BASE_URL
    llm_api_key: str = ""
    llm_model: str = DEFAULT_MODEL
    llm_temperature: float = 0.9
    llm_max_tokens: int = 800
    llm_timeout: float = 60.0

    # OneBot v11 / NapCat
    onebot_ws_url: str = DEFAULT_ONEBOT_WS_URL
    onebot_token: str = ""
    owner_qq: int | None = None
    allowed_users: frozenset[int] = frozenset()
    allowed_groups: frozenset[int] = frozenset()
    group_at_required: bool = True
    group_reply_at: bool = True
    auto_accept_friends: bool = True
    wake_words: tuple[str, ...] = DEFAULT_WAKE_WORDS

    # B 站
    bili_cookie: str = ""
    bili_user_agent: str = DEFAULT_UA
    bili_min_interval: float = 0.6

    # 电脑感知与安全操作（仅主人私聊）
    computer_enabled: bool = True
    computer_allowed_apps: tuple[str, ...] = ()
    activity_enabled: bool = True
    activity_interval_seconds: int = 45
    activity_idle_skip_seconds: int = 900
    activity_retention_days: int = 7
    activity_blocklist: tuple[str, ...] = DEFAULT_ACTIVITY_BLOCKLIST

    # B 站学习 / 技能库
    learning_enabled: bool = True
    learning_interval_minutes: int = 180
    learning_history_limit: int = 30
    learning_items_per_run: int = 2
    learning_min_watch_seconds: int = 120
    learning_keywords: tuple[str, ...] = ()
    skills_dir: Path = field(default_factory=lambda: APP_HOME / "skills")
    skill_max_chars: int = 5000
    skill_inject_limit: int = 3
    screen_read_enabled: bool = False
    screen_read_max_chars: int = 4000
    screen_read_timeout: float = 45.0
    dsh_enabled: bool = False
    dsh_chat: bool = False
    dsh_node: str = ""
    dsh_entry: str = ""
    dsh_profile: str = "headless"
    dsh_timeout: float = 240.0

    # 人格 / 记忆
    persona_name: str = DEFAULT_PERSONA_NAME
    persona_extra: str = ""
    persona_file: Path = field(default_factory=lambda: APP_HOME / "personas" / "dafeiyu.md")
    persona_max_chars: int = 6000
    timezone: str = DEFAULT_TIMEZONE
    history_turns: int = DEFAULT_HISTORY_TURNS
    max_reply_chars: int = DEFAULT_MAX_REPLY_CHARS
    db_path: Path = field(default_factory=lambda: APP_HOME / "data" / "dafeiyu.db")

    # 夜间自主（生物钟）
    night_enabled: bool = True
    night_windows: str = "20:00-24:00"
    night_wakes: int = 1
    night_skip_chance: float = 0.3
    night_min_gap_minutes: int = 10
    night_max_videos: int = 5
    night_deep_read: int = 2
    night_initial_delay: float = 45.0

    # 分享 / 日记
    share_enabled: bool = True
    share_min_score: int = 13
    share_daily_limit: int = 3
    silent_after_days: int = 3
    silent_stop_days: int = 7
    diary_push: bool = True
    diary_dir: Path = field(default_factory=lambda: APP_HOME / "diary")

    # 请求保护
    traffic_guard_enabled: bool = True
    bili_read_hourly: int = 600
    bili_read_daily: int = 3000
    bili_write_hourly: int = 12
    bili_write_daily: int = 30
    bili_write_gap_seconds: int = 120

    # 主动行为
    proactive_enabled: bool = True
    proactive_min_minutes: int = 50
    proactive_max_minutes: int = 140
    proactive_daily_limit: int = 12
    quiet_hours: tuple[int, int] | None = DEFAULT_QUIET_HOURS
    browse_enabled: bool = True
    browse_interval_minutes: int = 40
    browse_limit: int = 20
    share_cover: bool = False

    log_level: str = "INFO"

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        load_dotenv_file: bool = True,
        require_llm: bool = True,
    ) -> "Config":
        if load_dotenv_file:
            _load_dotenv()
        env = os.environ if env is None else env

        owner_raw = _get(env, "OWNER_QQ")
        owner: int | None = None
        if owner_raw:
            try:
                owner = int(owner_raw)
            except ValueError:
                raise ConfigError(f"OWNER_QQ 需要是 QQ 号，现在是：{owner_raw!r}") from None

        db_raw = _get(env, "DATABASE_PATH")
        db_path = _resolve_path(db_raw) if db_raw else APP_HOME / "data" / "dafeiyu.db"

        config = cls(
            llm_base_url=_get(env, "LLM_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL,
            llm_api_key=_get(env, "LLM_API_KEY", "") or "",
            llm_model=_get(env, "LLM_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
            llm_temperature=_as_float(env, "LLM_TEMPERATURE", 0.9),
            llm_max_tokens=_as_int(env, "LLM_MAX_TOKENS", 800),
            llm_timeout=_as_float(env, "LLM_TIMEOUT", 60.0),
            onebot_ws_url=_get(env, "ONEBOT_WS_URL", DEFAULT_ONEBOT_WS_URL) or DEFAULT_ONEBOT_WS_URL,
            onebot_token=_get(env, "ONEBOT_ACCESS_TOKEN", "") or "",
            owner_qq=owner,
            allowed_users=_as_int_set(env, "ALLOWED_USERS"),
            allowed_groups=_as_int_set(env, "ALLOWED_GROUPS"),
            group_at_required=_as_bool(env, "GROUP_AT_REQUIRED", True),
            group_reply_at=_as_bool(env, "GROUP_REPLY_AT", True),
            auto_accept_friends=_as_bool(env, "AUTO_ACCEPT_FRIENDS", True),
            wake_words=_as_words(env, "WAKE_WORDS", DEFAULT_WAKE_WORDS),
            computer_enabled=_as_bool(env, "COMPUTER_ENABLED", True),
            computer_allowed_apps=_as_words(env, "COMPUTER_ALLOWED_APPS", ()),
            activity_enabled=_as_bool(env, "ACTIVITY_ENABLED", True),
            activity_interval_seconds=_as_int(env, "ACTIVITY_INTERVAL_SECONDS", 45),
            activity_idle_skip_seconds=_as_int(env, "ACTIVITY_IDLE_SKIP_SECONDS", 900),
            activity_retention_days=_as_int(env, "ACTIVITY_RETENTION_DAYS", 7),
            activity_blocklist=_as_words(env, "ACTIVITY_BLOCKLIST", DEFAULT_ACTIVITY_BLOCKLIST),
            learning_enabled=_as_bool(env, "LEARNING_ENABLED", True),
            learning_interval_minutes=_as_int(env, "LEARNING_INTERVAL_MINUTES", 180),
            learning_history_limit=_as_int(env, "LEARNING_HISTORY_LIMIT", 30),
            learning_items_per_run=_as_int(env, "LEARNING_ITEMS_PER_RUN", 2),
            learning_min_watch_seconds=_as_int(env, "LEARNING_MIN_WATCH_SECONDS", 120),
            learning_keywords=_as_words(env, "LEARNING_KEYWORDS", ()),
            skills_dir=(
                _resolve_path(_get(env, "SKILLS_DIR"))
                if _get(env, "SKILLS_DIR")
                else APP_HOME / "skills"
            ),
            skill_max_chars=_as_int(env, "SKILL_MAX_CHARS", 5000),
            skill_inject_limit=_as_int(env, "SKILL_INJECT_LIMIT", 3),
            screen_read_enabled=_as_bool(env, "SCREEN_READ_ENABLED", False),
            screen_read_max_chars=_as_int(env, "SCREEN_READ_MAX_CHARS", 4000),
            screen_read_timeout=_as_float(env, "SCREEN_READ_TIMEOUT", 45.0),
            dsh_enabled=_as_bool(env, "DSH_ENABLED", False),
            dsh_chat=_as_bool(env, "DSH_CHAT", False),
            dsh_node=_get(env, "DSH_NODE", "") or "",
            dsh_entry=_get(env, "DSH_ENTRY", "") or "",
            dsh_profile=_get(env, "DSH_PROFILE", "headless") or "headless",
            dsh_timeout=_as_float(env, "DSH_TIMEOUT", 240.0),
            bili_cookie=_get(env, "BILI_COOKIE", "") or "",
            bili_user_agent=_get(env, "BILI_USER_AGENT", DEFAULT_UA) or DEFAULT_UA,
            bili_min_interval=_as_float(env, "BILI_MIN_INTERVAL", 0.6),
            night_enabled=_as_bool(env, "NIGHT_ENABLED", True),
            night_windows=_get(env, "NIGHT_WINDOWS", "20:00-24:00") or "20:00-24:00",
            night_wakes=_as_int(env, "NIGHT_WAKES", 1),
            night_skip_chance=_as_float(env, "NIGHT_SKIP_CHANCE", 0.3),
            night_min_gap_minutes=_as_int(env, "NIGHT_MIN_GAP_MINUTES", 10),
            night_max_videos=_as_int(env, "NIGHT_MAX_VIDEOS", 5),
            night_deep_read=_as_int(env, "NIGHT_DEEP_READ", 2),
            night_initial_delay=_as_float(env, "NIGHT_INITIAL_DELAY", 45.0),
            share_enabled=_as_bool(env, "SHARE_ENABLED", True),
            share_min_score=_as_int(env, "SHARE_MIN_SCORE", 13),
            share_daily_limit=_as_int(env, "SHARE_DAILY_LIMIT", 3),
            silent_after_days=_as_int(env, "SILENT_AFTER_DAYS", 3),
            silent_stop_days=_as_int(env, "SILENT_STOP_DAYS", 7),
            diary_push=_as_bool(env, "DIARY_PUSH", True),
            diary_dir=(
                _resolve_path(_get(env, "DIARY_DIR"))
                if _get(env, "DIARY_DIR")
                else APP_HOME / "diary"
            ),
            traffic_guard_enabled=_as_bool(env, "TRAFFIC_GUARD_ENABLED", True),
            bili_read_hourly=_as_int(env, "BILI_READ_HOURLY", 600),
            bili_read_daily=_as_int(env, "BILI_READ_DAILY", 3000),
            bili_write_hourly=_as_int(env, "BILI_WRITE_HOURLY", 12),
            bili_write_daily=_as_int(env, "BILI_WRITE_DAILY", 30),
            bili_write_gap_seconds=_as_int(env, "BILI_WRITE_GAP_SECONDS", 120),
            persona_name=_get(env, "PERSONA_NAME", DEFAULT_PERSONA_NAME) or DEFAULT_PERSONA_NAME,
            persona_extra=_get(env, "PERSONA_EXTRA", "") or "",
            persona_file=(
                _resolve_path(_get(env, "PERSONA_FILE"))
                if _get(env, "PERSONA_FILE")
                else APP_HOME / "personas" / "dafeiyu.md"
            ),
            persona_max_chars=_as_int(env, "PERSONA_MAX_CHARS", 6000),
            timezone=_get(env, "TIMEZONE", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE,
            history_turns=_as_int(env, "HISTORY_TURNS", DEFAULT_HISTORY_TURNS),
            max_reply_chars=_as_int(env, "MAX_REPLY_CHARS", DEFAULT_MAX_REPLY_CHARS),
            db_path=db_path,
            proactive_enabled=_as_bool(env, "PROACTIVE_ENABLED", True),
            proactive_min_minutes=_as_int(env, "PROACTIVE_MIN_MINUTES", 50),
            proactive_max_minutes=_as_int(env, "PROACTIVE_MAX_MINUTES", 140),
            proactive_daily_limit=_as_int(env, "PROACTIVE_DAILY_LIMIT", 12),
            quiet_hours=_as_quiet_hours(env, "QUIET_HOURS", DEFAULT_QUIET_HOURS),
            browse_enabled=_as_bool(env, "BROWSE_ENABLED", True),
            browse_interval_minutes=_as_int(env, "BROWSE_INTERVAL_MINUTES", 40),
            browse_limit=_as_int(env, "BROWSE_LIMIT", 20),
            share_cover=_as_bool(env, "SHARE_COVER", False),
            log_level=(_get(env, "LOG_LEVEL", "INFO") or "INFO").upper(),
        )
        config.validate(require_llm=require_llm)
        return config

    def validate(self, *, require_llm: bool = True) -> None:
        if require_llm and not self.llm_api_key:
            raise ConfigError(
                "缺少 LLM_API_KEY。把 .env.example 复制成 .env，填入 DeepSeek（或其他 OpenAI 兼容服务）的 API Key。"
            )
        if not self.llm_base_url.startswith(("http://", "https://")):
            raise ConfigError(f"LLM_BASE_URL 必须以 http:// 或 https:// 开头：{self.llm_base_url!r}")
        if not self.onebot_ws_url.startswith(("ws://", "wss://")):
            raise ConfigError(f"ONEBOT_WS_URL 必须以 ws:// 或 wss:// 开头：{self.onebot_ws_url!r}")
        if self.proactive_min_minutes > self.proactive_max_minutes:
            self.proactive_min_minutes, self.proactive_max_minutes = (
                self.proactive_max_minutes,
                self.proactive_min_minutes,
            )
        if self.proactive_min_minutes < 1:
            self.proactive_min_minutes = 1
        if self.proactive_max_minutes < self.proactive_min_minutes:
            self.proactive_max_minutes = self.proactive_min_minutes
        self.activity_interval_seconds = max(10, min(self.activity_interval_seconds, 600))
        self.activity_idle_skip_seconds = max(60, min(self.activity_idle_skip_seconds, 7200))
        self.activity_retention_days = max(1, min(self.activity_retention_days, 90))
        self.learning_interval_minutes = max(30, min(self.learning_interval_minutes, 1440))
        self.learning_history_limit = max(5, min(self.learning_history_limit, 50))
        self.learning_items_per_run = max(1, min(self.learning_items_per_run, 5))
        self.learning_min_watch_seconds = max(0, min(self.learning_min_watch_seconds, 3600))
        self.skill_max_chars = max(1000, min(self.skill_max_chars, 10000))
        self.skill_inject_limit = max(0, min(self.skill_inject_limit, 5))
        self.dsh_timeout = max(30.0, min(self.dsh_timeout, 600.0))
        self.screen_read_max_chars = max(500, min(self.screen_read_max_chars, 12000))
        self.screen_read_timeout = max(10.0, min(self.screen_read_timeout, 120.0))
        if self.persona_max_chars < 1000:
            self.persona_max_chars = 1000
        if self.persona_max_chars > 20000:
            self.persona_max_chars = 20000
        if self.history_turns < 0:
            self.history_turns = 0
        if self.browse_limit < 1:
            self.browse_limit = 1
        if self.browse_limit > 50:
            self.browse_limit = 50
        if self.browse_interval_minutes < 5:
            self.browse_interval_minutes = 5
        self.night_wakes = max(0, min(self.night_wakes, 12))
        self.night_skip_chance = max(0.0, min(self.night_skip_chance, 1.0))
        self.night_min_gap_minutes = max(1, min(self.night_min_gap_minutes, 180))
        self.night_max_videos = max(1, min(self.night_max_videos, 20))
        self.night_deep_read = max(1, min(self.night_deep_read, 5))
        self.share_min_score = max(0, min(self.share_min_score, 20))
        self.share_daily_limit = max(0, min(self.share_daily_limit, 10))
        self.silent_after_days = max(1, min(self.silent_after_days, 60))
        self.silent_stop_days = max(self.silent_after_days + 1, min(self.silent_stop_days, 120))
        self.bili_read_hourly = max(1, min(self.bili_read_hourly, 10000))
        self.bili_read_daily = max(1, min(self.bili_read_daily, 50000))
        self.bili_write_hourly = max(1, min(self.bili_write_hourly, 200))
        self.bili_write_daily = max(1, min(self.bili_write_daily, 1000))
        self.bili_write_gap_seconds = max(0, min(self.bili_write_gap_seconds, 3600))

    @property
    def proactive_session_id(self) -> str | None:
        return f"private:{self.owner_qq}" if self.owner_qq else None

    def allows_private(self, user_id: int) -> bool:
        if self.owner_qq and user_id == self.owner_qq:
            return True
        if self.allowed_users:
            return user_id in self.allowed_users
        # 没配白名单时只服务主人，避免陌生人一加就烧 token
        return False

    def allows_group(self, group_id: int) -> bool:
        return group_id in self.allowed_groups


def _resolve_path(value: str | None) -> Path:
    path = Path(value or "").expanduser()
    return path if path.is_absolute() else APP_HOME / path


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - 依赖没装时也能跑（用真实环境变量）
        return

    path = APP_HOME / ".env"
    if path.exists():
        load_dotenv(dotenv_path=path, override=False)
