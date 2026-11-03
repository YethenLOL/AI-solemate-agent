from __future__ import annotations

import pytest

from dafeiyu.dsh import DSHClient, _flatten_messages, _parse_response
from dafeiyu.llm import LLMError


def test_flatten_messages_keeps_roles_and_content() -> None:
    text = _flatten_messages(
        [
            {"role": "system", "content": "你是奥米"},
            {"role": "user", "content": "在吗"},
            {"role": "assistant", "content": "在的"},
            {"role": "tool", "content": "{}"},
        ]
    )
    assert "系统设定" in text
    assert "主人" in text
    assert "你之前的回复" in text
    assert "工具结果" in text
    assert "在吗" in text


def test_unavailable_dsh_reports_status_and_raises(monkeypatch) -> None:
    import dafeiyu.dsh as dsh_module

    monkeypatch.setattr(dsh_module, "find_node", lambda explicit="": "")
    monkeypatch.setattr(dsh_module, "find_entry", lambda explicit="": "")
    client = DSHClient(node="/does/not/exist", entry="/does/not/exist")
    assert not client.available
    assert "Node" in client.status() or "DSH" in client.status()
    with pytest.raises(LLMError):
        import asyncio

        asyncio.run(client.complete([{"role": "user", "content": "hi"}]))


def test_parse_tool_json_and_content() -> None:
    tools = [{"type": "function", "function": {"name": "bili_hot", "parameters": {}}}]
    call = _parse_response('{"tool":"bili_hot","arguments":{"limit":2}}', tools)
    assert call.tool_calls[0]["function"]["name"] == "bili_hot"
    assert '"limit": 2' in call.tool_calls[0]["function"]["arguments"]

    import json

    args_text = json.dumps({"limit": 2})
    string_args = _parse_response(json.dumps({"tool": "bili_hot", "arguments": args_text}), tools)
    assert string_args.tool_calls[0]["function"]["arguments"] == args_text

    content = _parse_response('```json\n{"content":"好的"}\n```', tools)
    assert content.content == "好的"

    plain = _parse_response("直接回答", tools)
    assert plain.content == "直接回答"
