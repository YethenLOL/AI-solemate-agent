from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from dafeiyu.persona import PersonaCard, build_system_prompt


def test_card_loads_replaces_placeholders_and_sections(tmp_path: Path) -> None:
    path = tmp_path / "card.md"
    path.write_text(
        "# 角色卡：{{name}}\n\n## 一、你是谁\n- 主人 QQ：{{owner_qq}}\n\n## 二、说话风格\n- 短句。\n",
        encoding="utf-8",
    )
    card = PersonaCard(path, name="小鱼", owner_qq=123456)
    text = card.load()
    assert "小鱼" in text
    assert "123456" in text
    assert "{{" not in text
    assert card.source == "file"

    headings = [heading for heading, _ in card.sections()]
    assert "一、你是谁" in headings
    assert "二、说话风格" in headings


def test_card_hot_reload(tmp_path: Path) -> None:
    path = tmp_path / "card.md"
    path.write_text("第一版", encoding="utf-8")
    card = PersonaCard(path, name="小鱼")
    assert card.load() == "第一版"

    path.write_text("第二版", encoding="utf-8")
    stamp = time.time_ns() + 1_000_000
    os.utime(path, ns=(stamp, stamp))
    assert card.load() == "第二版"


def test_card_truncates(tmp_path: Path) -> None:
    path = tmp_path / "card.md"
    path.write_text("a" * 1500, encoding="utf-8")
    card = PersonaCard(path, name="小鱼", max_chars=1000)
    text = card.load()
    assert len(text) < 1200
    assert "已截断" in text


def test_missing_card_uses_fallback(tmp_path: Path) -> None:
    card = PersonaCard(tmp_path / "missing.md", name="小鱼")
    text = card.load()
    assert "小鱼" in text
    assert card.source == "fallback"


def test_build_prompt_with_dynamic_context() -> None:
    prompt = build_system_prompt(
        name="小鱼",
        now=datetime(2026, 11, 3, 14, 30),
        facts=["喜欢冰美式"],
        videos=[{"title": "鱼的一天", "author": "UP", "url": "https://example.com"}],
        extra="更粘人",
        persona_text="CARD",
    )
    assert prompt.startswith("CARD")
    assert "【额外设定】" in prompt and "更粘人" in prompt
    assert "喜欢冰美式" in prompt
    assert "鱼的一天" in prompt
    assert "下午" in prompt
    assert "QQ 私聊" in prompt


def test_group_prompt_has_privacy_note() -> None:
    prompt = build_system_prompt(
        name="小鱼",
        now=datetime(2026, 11, 3, 23, 0),
        in_group=True,
        persona_text="CARD",
    )
    assert "QQ 群" in prompt
    assert "别透露私聊" in prompt
    assert "深夜" in prompt
