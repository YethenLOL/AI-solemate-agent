"""DeepSeek Harness（DSH）本地运行时适配。

DSH 的 headless profile 适合没有工具调用的纯生成任务（例如把 B 站视频整理成技能卡）。
QQ 对话的工具调用仍由本地 Agent 负责；DSH 不可用时会回退到 OpenAI 兼容接口。
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

from .llm import LLMError, LLMResponse, UsageSink
from .usage import estimate_tokens

NODE_MIN = (22, 19)
NO_TOOLS_PATCH = """\
- id: tool-bash
  disabled: true
- id: tool-pwsh
  disabled: true
- id: tool-jobs
  disabled: true
- id: tool-fs
  disabled: true
- id: tool-fs-search
  disabled: true
- id: tool-skill
  disabled: true
- id: tool-subagent-control
  disabled: true
- id: tool-subagent-list-agents
  disabled: true
- id: tool-subagent
  disabled: true
- id: tool-subagent-fork
  disabled: true
- id: tool-workflow
  disabled: true
- id: tool-web
  disabled: true
- id: tool-todo
  disabled: true
- id: tool-goal
  disabled: true
- id: tool-working-directory
  disabled: true
"""
ENTRY_GLOBS = (
    "~/.npm/_npx/*/node_modules/@deepseek-ai/dsh/lib/bin.js",
    "~/Library/Application Support/Dafeiyu/runtime/dsh/lib/bin.js",
    "~/dafeiyu-qq/node_modules/@deepseek-ai/dsh/lib/bin.js",
)


def _version_tuple(value: str) -> tuple[int, int]:
    match = re.search(r"v?(\d+)\.(\d+)", value)
    if not match:
        return (0, 0)
    return int(match.group(1)), int(match.group(2))


def find_node(explicit: str = "") -> str:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("DSH_NODE"):
        candidates.append(os.environ["DSH_NODE"])
    found = shutil.which("node")
    if found:
        candidates.append(found)
    candidates.extend(sorted(glob.glob(str(Path.home() / ".nvm/versions/node/*/bin/node")), reverse=True))
    candidates.extend(["/opt/homebrew/bin/node", "/usr/local/bin/node"])
    for candidate in dict.fromkeys(candidates):
        path = Path(candidate).expanduser()
        if not path.exists() or not os.access(path, os.X_OK):
            continue
        try:
            output = subprocess.check_output([str(path), "--version"], text=True, timeout=5).strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if _version_tuple(output) >= NODE_MIN:
            return str(path)
    return ""


def find_entry(explicit: str = "") -> str:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("DSH_ENTRY"):
        candidates.append(os.environ["DSH_ENTRY"])
    for pattern in ENTRY_GLOBS:
        candidates.extend(glob.glob(str(Path(pattern).expanduser())))
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
    return ""


def _flatten_messages(messages: Sequence[dict[str, Any]]) -> str:
    parts: list[str] = []
    labels = {"system": "系统设定", "user": "主人", "assistant": "你之前的回复", "tool": "工具结果"}
    for message in messages:
        role = labels.get(str(message.get("role")), str(message.get("role")))
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(f"【{role}】\n{content.strip()}")
        elif content:
            parts.append(f"【{role}】\n{json.dumps(content, ensure_ascii=False)}")
    return "\n\n".join(parts)


def _tool_protocol(tools: Sequence[dict[str, Any]]) -> str:
    catalog = []
    for tool in tools:
        fn = tool.get("function") or {}
        if fn.get("name"):
            catalog.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                }
            )
    return (
        "你可以调用主人环境里的本地工具。可用工具如下（JSON Schema）：\n"
        + json.dumps(catalog, ensure_ascii=False)
        + "\n\n只输出一个 JSON 对象，不要 Markdown、不要解释：\n"
        '{"tool":"工具名","arguments":{...}}\n'
        "或者任务不需要工具时：\n"
        '{"content":"给主人的最终回复"}\n'
        "不要使用 DSH 自带的文件/终端工具（已被禁用）。"
    )


def _parse_response(text: str, tools: Sequence[dict[str, Any]] | None) -> LLMResponse:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
    allowed = {
        (tool.get("function") or {}).get("name")
        for tool in (tools or [])
        if (tool.get("function") or {}).get("name")
    }
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        if isinstance(obj.get("content"), str):
            return LLMResponse(content=obj["content"])
        calls = obj.get("tool_calls")
        if isinstance(calls, list) and calls:
            first = calls[0]
            if isinstance(first, dict):
                fn = first.get("function") or {}
                name = fn.get("name")
                args = fn.get("arguments") or "{}"
                if name and (not allowed or name in allowed):
                    return LLMResponse(
                        tool_calls=[
                            {
                                "id": str(first.get("id") or "dsh_call"),
                                "type": "function",
                                "function": {"name": name, "arguments": args},
                            }
                        ]
                    )
        name = obj.get("tool") or obj.get("name")
        args = obj.get("arguments", obj.get("args", {}))
        if name and (not allowed or name in allowed):
            return LLMResponse(
                tool_calls=[
                    {
                        "id": "dsh_call",
                        "type": "function",
                        "function": {
                            "name": str(name),
                            "arguments": (
                                args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                            ),
                        },
                    }
                ]
            )
    return LLMResponse(content=cleaned)


class DSHClient:
    """通过 `dsh --profile headless` 调用本地 Harness。"""

    supports_tools = True

    def __init__(
        self,
        *,
        node: str = "",
        entry: str = "",
        profile: str = "headless",
        api_key: str = "",
        timeout: float = 240.0,
        dsh_home: str = "",
        workdir: str | Path | None = None,
        usage_sink: UsageSink | None = None,
    ) -> None:
        self.node = find_node(node)
        self.entry = find_entry(entry)
        self.profile = profile or "headless"
        self.api_key = api_key
        self.timeout = timeout
        self.dsh_home = dsh_home
        self.usage_sink = usage_sink
        self.workdir = Path(workdir).expanduser() if workdir else Path.home() / ".dsh"
        try:
            self.workdir.mkdir(parents=True, exist_ok=True)
            self.patch_path = self.workdir / ".dsh-no-tools.yml"
            self.patch_path.write_text(NO_TOOLS_PATCH, encoding="utf-8")
        except OSError:
            self.patch_path = None
        self.last_error = ""

    @property
    def available(self) -> bool:
        return bool(self.node and self.entry and self.patch_path and self.patch_path.is_file())

    def status(self) -> str:
        if not self.node:
            return "找不到 Node >= 22.19（可设置 DSH_NODE）"
        if not self.entry:
            return "找不到 DSH 入口（可设置 DSH_ENTRY）"
        if not self.patch_path or not self.patch_path.is_file():
            return "无法写入禁用原生工具的 DSH patch（检查工作目录权限）"
        return f"Node {self.node}；入口 {self.entry}"

    def _command(self, task: str) -> list[str]:
        # DSH 的 bin.js 依赖 import.meta.main；这里显式调用 runCli，兼容 Node 22。
        loader = f"import({json.dumps(self.entry)}).then(m => m.runCli())"
        command = [self.node, "-e", loader, "--", "--profile", self.profile]
        if self.patch_path:
            command.extend(["--patch", str(self.patch_path)])
        command.append(task)
        return command

    async def complete(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        source: str = "chat",
    ) -> LLMResponse:
        if not self.available:
            raise LLMError(f"DSH 不可用：{self.status()}")
        prompt = _flatten_messages(messages)
        if tools:
            prompt = _tool_protocol(tools) + "\n\n" + prompt
        if not prompt.strip():
            raise LLMError("DSH 收到的提示词为空")
        env = os.environ.copy()
        if self.api_key:
            env["DEEPSEEK_API_KEY"] = self.api_key
        if self.dsh_home:
            env["DSH_HOME"] = self.dsh_home
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command("-"),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workdir,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=self.timeout
            )
        except asyncio.TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise LLMError(f"DSH 超时（{self.timeout:.0f}s）") from exc
        except OSError as exc:
            raise LLMError(f"启不动 DSH：{exc}") from exc
        text = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            self.last_error = err[-800:]
            raise LLMError(f"DSH 退出码 {proc.returncode}：{err[-300:] or '无错误输出'}")
        if not text:
            raise LLMError(f"DSH 没有输出：{err[-300:] or '无错误输出'}")
        response = _parse_response(text, tools)
        if not response.usage:
            prompt_tokens = estimate_tokens(prompt)
            completion_tokens = estimate_tokens(text)
            response.usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "estimated": True,
            }
        self._report_usage(response.usage, source)
        return response

    def _report_usage(self, usage: dict[str, Any], source: str) -> None:
        if not self.usage_sink or not usage:
            return
        try:
            prompt = max(int(usage.get("prompt_tokens") or 0), 0)
            completion = max(int(usage.get("completion_tokens") or 0), 0)
            payload = {
                "model": "dsh",
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

    async def aclose(self) -> None:
        return None
