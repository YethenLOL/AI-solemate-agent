from __future__ import annotations

import json

from dafeiyu.learning import (
    collect_links,
    collect_tool_names,
    parse_distillation,
    render_skill_card,
)

DATA = {
    "name": "本地跑通 STM32 点灯",
    "goal": "用 STM32CubeIDE 编译并烧录一个 LED 闪烁程序",
    "when_to_use": ["第一次拿到开发板", "验证工具链是否装好"],
    "tools": [
        {"name": "STM32CubeIDE", "kind": "app", "install": "官网下载安装包", "url": "https://www.st.com/en/development-tools/stm32cubeide.html"},
        {"name": "ST-Link", "kind": "hardware", "install": "开发板自带", "url": ""},
    ],
    "websites": [
        {"name": "STM32 文档", "url": "https://www.st.com/resource/en/reference_manual/rm0008.pdf", "purpose": "寄存器说明"}
    ],
    "prerequisites": ["安装好驱动"],
    "steps": [
        {"title": "新建工程", "detail": "选择对应芯片型号", "command": "", "url": "https://wiki.st.com/stm32mcu"},
        {"title": "编译烧录", "detail": "点击 Run", "command": "STM32_Programmer_CLI -c port=SWD -w build/app.hex", "url": ""},
    ],
    "verification": ["LED 每 1 秒闪烁"],
    "pitfalls": ["BOOT0 接高会进不了用户程序"],
    "references": [{"title": "视频教程", "url": "https://www.bilibili.com/video/BV1xx411c7mD"}],
    "confidence": "medium",
}


def test_parse_distillation_plain_and_fenced() -> None:
    assert parse_distillation(json.dumps(DATA, ensure_ascii=False)) == DATA
    fenced = "好的，结果如下：\n```json\n" + json.dumps(DATA, ensure_ascii=False) + "\n```\n"
    assert parse_distillation(fenced) == DATA
    assert parse_distillation("只有一句话") is None
    assert parse_distillation("") is None


def test_render_skill_card_has_tools_steps_and_links() -> None:
    card = render_skill_card(DATA)

    assert card.startswith("# 本地跑通 STM32 点灯")
    assert "> 用 STM32CubeIDE 编译并烧录一个 LED 闪烁程序" in card
    assert "## 需要的工具" in card
    assert "| STM32CubeIDE | app | 官网下载安装包 | https://www.st.com/en/development-tools/stm32cubeide.html |" in card
    assert "| ST-Link | hardware | 开发板自带 | — |" in card
    assert "## 用到的网站" in card
    assert "[STM32 文档](https://www.st.com/resource/en/reference_manual/rm0008.pdf) — 寄存器说明" in card
    assert "## 前置条件" in card
    assert "1. **新建工程** — 选择对应芯片型号" in card
    assert "STM32_Programmer_CLI -c port=SWD -w build/app.hex" in card
    assert "## 验证" in card
    assert "## 易错点" in card
    assert "## 参考资料" in card
    assert "[视频教程](https://www.bilibili.com/video/BV1xx411c7mD)" in card
    assert "蒸馏置信度：medium" in card


def test_render_skill_card_escapes_table_pipes() -> None:
    card = render_skill_card(
        {"name": "测试", "tools": [{"name": "a|b", "kind": "cli", "install": "x", "url": ""}]}
    )
    assert "| a\\|b | cli | x | — |" in card


def test_render_skill_card_handles_empty_sections() -> None:
    card = render_skill_card({"name": "只跳一步", "steps": [{"title": "打开 App"}]})
    assert "## 需要的工具" not in card
    assert "## 操作步骤" in card
    assert "1. **打开 App**" in card


def test_collect_tools_and_links_dedupes() -> None:
    data = {
        "tools": [
            {"name": "A", "url": "https://example.com/a"},
            {"name": "B", "url": "https://example.com/a"},
            {"name": "", "url": "https://example.com/empty"},
        ],
        "websites": [{"url": "https://example.com/site"}],
        "references": [{"url": "https://example.com/a"}],
        "steps": [{"command": "curl https://example.com/cmd", "url": ""}],
    }

    assert collect_tool_names(data) == ["A", "B"]
    links = collect_links(data)
    assert links == [
        "https://example.com/a",
        "https://example.com/empty",
        "https://example.com/site",
        "https://example.com/cmd",
    ]


def test_collect_links_drops_truncated_bv_links() -> None:
    data = {
        "references": [
            {"url": "https://www.bilibili.com/video/BV19S4y1v"},
            {"url": "https://www.bilibili.com/video/BV1Sz4y1U77N"},
        ]
    }
    assert collect_links(data) == ["https://www.bilibili.com/video/BV1Sz4y1U77N"]
