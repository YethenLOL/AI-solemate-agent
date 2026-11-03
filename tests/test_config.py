from __future__ import annotations

import pytest

from dafeiyu.config import Config, ConfigError


def env(**overrides: str) -> dict[str, str]:
    base = {
        "LLM_API_KEY": "sk-test",
        "OWNER_QQ": "123456",
        "ONEBOT_WS_URL": "ws://127.0.0.1:3001",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def test_defaults() -> None:
    config = Config.from_env(env(), load_dotenv_file=False)
    assert config.llm_model == "deepseek-chat"
    assert config.owner_qq == 123456
    assert config.quiet_hours == (1, 8)
    assert config.proactive_enabled is True
    assert config.db_path.name == "dafeiyu.db"


def test_parses_lists_and_booleans() -> None:
    config = Config.from_env(
        env(
            ALLOWED_USERS="111, 222，333",
            ALLOWED_GROUPS="9001;9002",
            PROACTIVE_ENABLED="false",
            GROUP_AT_REQUIRED="0",
            QUIET_HOURS="23-7",
            WAKE_WORDS="肥鱼，大鱼",
            DATABASE_PATH="/tmp/x.db",
        ),
        load_dotenv_file=False,
    )
    assert config.allowed_users == frozenset({111, 222, 333})
    assert config.allowed_groups == frozenset({9001, 9002})
    assert config.proactive_enabled is False
    assert config.group_at_required is False
    assert config.auto_accept_friends is True
    assert config.quiet_hours == (23, 7)
    assert config.wake_words == ("肥鱼", "大鱼")
    assert str(config.db_path) == "/tmp/x.db"


def test_auto_accept_friends_can_be_disabled() -> None:
    config = Config.from_env(env(AUTO_ACCEPT_FRIENDS="false"), load_dotenv_file=False)
    assert config.auto_accept_friends is False


def test_persona_file_and_preview_without_key(tmp_path) -> None:
    config = Config.from_env(
        env(
            LLM_API_KEY=None,
            PERSONA_FILE=str(tmp_path / "card.md"),
            PERSONA_MAX_CHARS="7000",
        ),
        load_dotenv_file=False,
        require_llm=False,
    )
    assert config.persona_file == tmp_path / "card.md"
    assert config.persona_max_chars == 7000
    assert config.llm_api_key == ""


def test_computer_and_activity_config() -> None:
    config = Config.from_env(
        env(
            COMPUTER_ENABLED="false",
            COMPUTER_ALLOWED_APPS="Safari, 微信",
            ACTIVITY_ENABLED="0",
            ACTIVITY_INTERVAL_SECONDS="5",
            ACTIVITY_IDLE_SKIP_SECONDS="10",
            ACTIVITY_RETENTION_DAYS="999",
            ACTIVITY_BLOCKLIST="Secret, Passwords",
        ),
        load_dotenv_file=False,
    )
    assert config.computer_enabled is False
    assert config.computer_allowed_apps == ("Safari", "微信")
    assert config.activity_enabled is False
    assert config.activity_interval_seconds == 10
    assert config.activity_idle_skip_seconds == 60
    assert config.activity_retention_days == 90
    assert config.activity_blocklist == ("Secret", "Passwords")


def test_default_activity_blocklist() -> None:
    config = Config.from_env(env(), load_dotenv_file=False)
    assert "1Password" in config.activity_blocklist


def test_quiet_hours_off() -> None:
    config = Config.from_env(env(QUIET_HOURS="off"), load_dotenv_file=False)
    assert config.quiet_hours is None


def test_swaps_bad_interval() -> None:
    config = Config.from_env(
        env(PROACTIVE_MIN_MINUTES="200", PROACTIVE_MAX_MINUTES="60"),
        load_dotenv_file=False,
    )
    assert config.proactive_min_minutes == 60
    assert config.proactive_max_minutes == 200


@pytest.mark.parametrize("bad", ["abc", "1.5"])
def test_invalid_int_reports_key(bad: str) -> None:
    with pytest.raises(ConfigError, match="PROACTIVE_MIN_MINUTES"):
        Config.from_env(env(PROACTIVE_MIN_MINUTES=bad), load_dotenv_file=False)


def test_missing_api_key() -> None:
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        Config.from_env(env(LLM_API_KEY=None), load_dotenv_file=False)


def test_permissions() -> None:
    config = Config.from_env(env(), load_dotenv_file=False)
    assert config.allows_private(123456)
    assert not config.allows_private(999)
    assert not config.allows_group(1)

    open_config = Config.from_env(
        env(ALLOWED_USERS="777", ALLOWED_GROUPS="42"),
        load_dotenv_file=False,
    )
    assert open_config.allows_private(777)
    assert not open_config.allows_private(888)
    assert open_config.allows_group(42)


def test_persona_session_id() -> None:
    config = Config.from_env(env(), load_dotenv_file=False)
    assert config.proactive_session_id == "private:123456"


def test_learning_and_dsh_config(tmp_path) -> None:
    config = Config.from_env(
        env(
            LEARNING_ENABLED="false",
            LEARNING_INTERVAL_MINUTES="5",
            LEARNING_ITEMS_PER_RUN="99",
            SKILLS_DIR=str(tmp_path / "skills"),
            SKILL_INJECT_LIMIT="9",
            DSH_ENABLED="true",
            DSH_CHAT="true",
            DSH_PROFILE="headless",
            DSH_TIMEOUT="5",
        ),
        load_dotenv_file=False,
    )
    assert config.learning_enabled is False
    assert config.learning_interval_minutes == 30
    assert config.learning_items_per_run == 5
    assert config.skills_dir == tmp_path / "skills"
    assert config.skill_inject_limit == 5
    assert config.dsh_enabled is True
    assert config.dsh_chat is True
    assert config.dsh_profile == "headless"
    assert config.dsh_timeout == 30.0
