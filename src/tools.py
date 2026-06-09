import asyncio
import os
import sys
from typing import Any

import httpx

BASE_URL = os.environ["ELYOS_BASE_URL"].rstrip("/")
API_KEY = os.environ["ELYOS_API_KEY"]

_HEADERS = {"X-API-Key": API_KEY}
_WEATHER_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_RESEARCH_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_MAX_RETRIES = 3
_RETRY_CAP_SECONDS = 10.0


def _check_throttled(data: dict) -> bool:
    return isinstance(data, dict) and data.get("status") == "throttled"


def _parse_error(data: dict) -> str:
    if "error" in data:
        return data["error"]
    if "detail" in data:
        detail = data["detail"]
        msgs = [d.get("msg", str(d)) for d in detail] if isinstance(detail, list) else [str(detail)]
        return "; ".join(msgs)
    if "message" in data:
        return data["message"]
    return str(data)


async def _call_api(
    endpoint: str,
    params: dict[str, str],
    timeout: httpx.Timeout,
    default_wait: float,
) -> dict[str, Any]:
    """Shared GET with throttle detection and retry (Quirk 1)."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        for _ in range(_MAX_RETRIES):
            try:
                r = await client.get(f"{BASE_URL}/{endpoint}", params=params, headers=_HEADERS)
                data = r.json()

                if _check_throttled(data):
                    wait = min(float(data.get("retry_after_seconds", default_wait)), _RETRY_CAP_SECONDS)
                    print(f"\n  [rate-limited, retrying in {wait:.0f}s]", file=sys.stderr)
                    await asyncio.sleep(wait)
                    continue

                if not r.is_success:
                    return {"error": _parse_error(data), "status_code": r.status_code}

                return data

            except httpx.TimeoutException:
                return {"error": f"{endpoint} API timed out"}
            except httpx.RequestError as e:
                return {"error": f"network error: {e}"}
            except Exception as e:
                return {"error": f"{type(e).__name__}: {e}"}

    return {"error": f"{endpoint} API is rate-limited — please try again shortly"}


async def get_weather(location: str) -> dict[str, Any]:
    return await _call_api("weather", {"location": location}, _WEATHER_TIMEOUT, default_wait=2.0)


async def research_topic(topic: str) -> dict[str, Any]:
    return await _call_api("research", {"topic": topic}, _RESEARCH_TIMEOUT, default_wait=5.0)


TOOL_DEFINITIONS = [
    {
        "name": "get_weather",
        "description": "Get current weather for a city. Fast response (~200ms).",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name, e.g. London, Tokyo"},
            },
            "required": ["location"],
        },
    },
    {
        "name": "research_topic",
        "description": "Research a topic in depth. Takes 3-8 seconds. Use for questions requiring detailed research.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic to research, e.g. 'solar energy', 'climate change'"},
            },
            "required": ["topic"],
        },
    },
]

TOOL_MAP = {
    "get_weather": get_weather,
    "research_topic": research_topic,
}
