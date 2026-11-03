from __future__ import annotations

from typing import Any

import httpx

from dafeiyu.llm import LLMClient


def make_client(sink, *, status: int = 200, body: dict[str, Any] | None = None) -> tuple[LLMClient, httpx.AsyncClient]:
    def handler(request: httpx.Request) -> httpx.Response:
        if body is not None:
            return httpx.Response(status, json=body)
        return httpx.Response(status, text="")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LLMClient(
        base_url="https://example.test/v1",
        api_key="test-key",
        model="deepseek-chat",
        http_client=http_client,
        max_retries=0,
        usage_sink=sink,
    )
    return client, http_client


async def test_usage_sink_receives_provider_usage() -> None:
    captured: list[dict[str, Any]] = []
    client, http_client = make_client(
        captured.append,
        body={
            "choices": [{"message": {"content": "咕噜"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        },
    )
    try:
        response = await client.complete(
            [{"role": "user", "content": "在吗"}], source="learning"
        )
    finally:
        await client.aclose()
        await http_client.aclose()

    assert response.content == "咕噜"
    assert captured == [
        {
            "model": "deepseek-chat",
            "source": "learning",
            "prompt_tokens": 12,
            "completion_tokens": 3,
            "total_tokens": 15,
            "estimated": False,
        }
    ]


async def test_usage_sink_fills_missing_total() -> None:
    captured: list[dict[str, Any]] = []
    client, http_client = make_client(
        captured.append,
        body={
            "choices": [{"message": {"content": "咕噜"}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 6},
        },
    )
    try:
        await client.complete([{"role": "user", "content": "hi"}], source="chat")
    finally:
        await client.aclose()
        await http_client.aclose()

    assert captured[0]["total_tokens"] == 10


async def test_usage_sink_failure_does_not_break_completion() -> None:
    def sink(_payload: dict[str, Any]) -> None:
        raise RuntimeError("统计炸了")

    client, http_client = make_client(
        sink,
        body={"choices": [{"message": {"content": "咕噜"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
    )
    try:
        response = await client.complete([{"role": "user", "content": "hi"}], source="chat")
    finally:
        await client.aclose()
        await http_client.aclose()

    assert response.content == "咕噜"
