import asyncio
import json
import os
from typing import Any

import anthropic
from rich.console import Console
from rich.status import Status

from tools import TOOL_DEFINITIONS, TOOL_MAP

MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")


_client = anthropic.AsyncAnthropic()
_console = Console(stderr=True)

SYSTEM = (
    "You are a helpful assistant with access to real-time weather data and a research tool. "
    "Use get_weather for weather questions (it's fast). "
    "Use research_topic for questions needing in-depth information (it takes a few seconds). "
    "If a tool returns an error, explain it clearly to the user."
)


async def _run_tool(name: str, tool_input: dict[str, Any], label: str) -> str:
    """Run a tool with a spinner, return JSON-serialised result string."""
    fn = TOOL_MAP.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool: {name}"})

    with Status(label, console=_console, spinner="dots"):
        result = await fn(**tool_input)

    return json.dumps(result)


async def run_turn(user_input: str, history: list[dict]) -> None:
    """
    Process one conversational turn. Mutates `history` in place.
    Streams text to stdout; loops until the model stops requesting tool calls.
    Raises asyncio.CancelledError on Ctrl+C — callers should handle it.
    """
    checkpoint = len(history)  # restore point: roll back on cancellation

    if user_input:
        history.append({"role": "user", "content": user_input})

    try:
        while True:
            async with _client.messages.stream(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM,
                messages=history,
                tools=TOOL_DEFINITIONS,
            ) as stream:
                async for text in stream.text_stream:
                    print(text, end="", flush=True)
                final = await stream.get_final_message()

            content_blocks = final.content
            tool_uses = [b for b in content_blocks if b.type == "tool_use"]

            history.append({"role": "assistant", "content": content_blocks})

            if not tool_uses:
                print()
                break

            tool_results = []
            for block in tool_uses:
                topic_or_loc = next(iter(block.input.values()), block.name)
                label = (
                    f"Researching {topic_or_loc}... (Ctrl+C to cancel)"
                    if block.name == "research_topic"
                    else f"Getting weather for {topic_or_loc}..."
                )
                result_str = await _run_tool(block.name, block.input, label)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

            history.append({"role": "user", "content": tool_results})

    except asyncio.CancelledError:
        del history[checkpoint:]  # drop partial turn so next turn starts clean
        raise
