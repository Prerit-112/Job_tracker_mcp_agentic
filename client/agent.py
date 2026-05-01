"""OpenAI tool-calling loop over a live MCP stdio session."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI

from client.mcp_utils import format_tool_result, server_parameters, tool_to_openai_dict


@dataclass
class ToolTraceEntry:
    name: str
    arguments: dict[str, Any]
    result_preview: str


@dataclass
class AgentTurnResult:
    messages: list[dict[str, Any]]
    assistant_text: str
    trace: list[ToolTraceEntry] = field(default_factory=list)
    error: str | None = None


def _assistant_message_payload(msg: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        payload["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    return payload


async def run_agent_turn(
    messages: list[dict[str, Any]],
    user_text: str,
    *,
    project_root: Path,
    api_key: str,
    model: str,
    max_steps: int = 14,
) -> AgentTurnResult:
    trace: list[ToolTraceEntry] = []
    msgs = [*messages, {"role": "user", "content": user_text}]
    client = AsyncOpenAI(api_key=api_key)

    try:
        async with stdio_client(server_parameters(project_root)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                openai_tools = [tool_to_openai_dict(t) for t in listed.tools]

                for _ in range(max_steps):
                    resp = await client.chat.completions.create(
                        model=model,
                        messages=msgs,
                        tools=openai_tools,
                        tool_choice="auto",
                        temperature=0.2,
                    )
                    choice = resp.choices[0]
                    msg = choice.message

                    if choice.finish_reason == "stop" or not msg.tool_calls:
                        text = (msg.content or "").strip()
                        msgs.append(_assistant_message_payload(msg))
                        return AgentTurnResult(messages=msgs, assistant_text=text, trace=trace)

                    msgs.append(_assistant_message_payload(msg))

                    for tc in msg.tool_calls:
                        name = tc.function.name
                        try:
                            raw = tc.function.arguments or "{}"
                            args = json.loads(raw)
                        except json.JSONDecodeError:
                            args = {"_raw": tc.function.arguments}

                        result = await session.call_tool(name, args)
                        body = format_tool_result(result)
                        trace.append(
                            ToolTraceEntry(
                                name=name,
                                arguments=args,
                                result_preview=body[:3500],
                            )
                        )
                        msgs.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": body,
                            }
                        )

                return AgentTurnResult(
                    messages=msgs,
                    assistant_text="Stopped: too many tool rounds (raise max steps in sidebar).",
                    trace=trace,
                    error="max_steps",
                )
    except Exception as e:  # noqa: BLE001
        return AgentTurnResult(
            messages=msgs,
            assistant_text="",
            trace=trace,
            error=str(e),
        )
