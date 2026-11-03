"""极简 OpenAI 兼容 LLM 客户端（DeepSeek / Moonshot / Ollama 等都能用）。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

UsageSink = Callable[[dict[str, Any]], None]


class LLMError(RuntimeError):
    pass


@dataclass(slots=True)
class LLMResponse:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.9,
        max_tokens: int = 800,
        timeout: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 2,
        usage_sink: UsageSink | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.usage_sink = usage_sink
        self._client = http_client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = http_client is None

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        source: str = "chat",
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post(self.endpoint, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last_error = LLMError(f"连不上模型服务：{exc}")
            else:
                if response.status_code == 200:
                    result = _parse_response(response.text)
                    self._report_usage(result.usage, source)
                    return result
                body = response.text[:500]
                last_error = LLMError(f"模型服务返回 {response.status_code}：{body}")
                if response.status_code not in (408, 409, 429) and response.status_code < 500:
                    raise last_error
            if attempt < self.max_retries:
                await asyncio.sleep(1.5 * (attempt + 1))
        raise last_error or LLMError("模型调用失败")

    def _report_usage(self, usage: dict[str, Any], source: str) -> None:
        if not self.usage_sink or not usage:
            return
        try:
            prompt = max(int(usage.get("prompt_tokens") or 0), 0)
            completion = max(int(usage.get("completion_tokens") or 0), 0)
            payload = {
                "model": self.model,
                "source": source,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": max(int(usage.get("total_tokens") or 0), 0) or (prompt + completion),
                "estimated": bool(usage.get("estimated")),
            }
        except (TypeError, ValueError):
            return
        try:
            self.usage_sink(payload)
        except Exception:  # 统计失败不影响正常回复
            pass


def _parse_response(text: str) -> LLMResponse:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"模型返回的不是 JSON：{text[:300]}") from exc

    choices = data.get("choices") or []
    if not choices:
        raise LLMError(f"模型没有返回 choices：{text[:300]}")

    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    raw_calls = message.get("tool_calls") or []
    tool_calls: list[dict[str, Any]] = []
    for index, call in enumerate(raw_calls):
        function = call.get("function") or {}
        tool_calls.append(
            {
                "id": call.get("id") or f"call_{index}",
                "type": "function",
                "function": {
                    "name": function.get("name") or "",
                    "arguments": function.get("arguments") or "{}",
                },
            }
        )

    return LLMResponse(
        content=content.strip(),
        tool_calls=tool_calls,
        finish_reason=choices[0].get("finish_reason"),
        usage=data.get("usage") or {},
    )
