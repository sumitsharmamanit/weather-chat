"""Tests for API quirks discovered during probing."""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ELYOS_BASE_URL", "https://elyos-interview-907656039105.europe-west2.run.app")
os.environ.setdefault("ELYOS_API_KEY", "test-key")


def _resp(data: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.is_success = (200 <= status < 300)
    r.json.return_value = data
    return r


def _client(response: MagicMock) -> MagicMock:
    c = AsyncMock()
    c.get.return_value = response
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


# ── Quirk 1: throttle returns HTTP 200, not 429 ──────────────────────────────

@pytest.mark.asyncio
async def test_throttled_response_retries_and_returns_error():
    """Quirk 1: {"status":"throttled"} on HTTP 200 — must check body, not status code."""
    throttle = {"status": "throttled", "retry_after_seconds": 1, "data": None}
    mock = _client(_resp(throttle, status=200))

    with patch("tools.httpx.AsyncClient", return_value=mock), \
         patch("tools.asyncio.sleep", new_callable=AsyncMock):
        from tools import get_weather
        result = await get_weather("London")

    assert "error" in result
    assert "rate-limited" in result["error"].lower()


# ── Quirk 2: dual weather schema (flat vs conditions[]) ──────────────────────

@pytest.mark.asyncio
async def test_weather_flat_schema():
    data = {"location": "London", "temperature_c": 18.1, "condition": "Cloudy", "humidity": 42}
    with patch("tools.httpx.AsyncClient", return_value=_client(_resp(data))):
        from tools import get_weather
        result = await get_weather("London")
    assert result["temperature_c"] == 18.1
    assert result["condition"] == "Cloudy"


@pytest.mark.asyncio
async def test_weather_nested_conditions_schema():
    data = {
        "location": "London",
        "conditions": [{"temperature_c": 18.1, "condition": "Cloudy", "humidity": 42}],
        "note": "multiple readings",
    }
    with patch("tools.httpx.AsyncClient", return_value=_client(_resp(data))):
        from tools import get_weather
        result = await get_weather("London")
    assert "conditions" in result


# ── Quirk 3: empty {} body on HTTP 200 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_research_empty_body_returns_no_error():
    """Quirk 3: {} body must not raise KeyError — return it as-is."""
    with patch("tools.httpx.AsyncClient", return_value=_client(_resp({}))):
        from tools import research_topic
        result = await research_topic("climate change")
    assert isinstance(result, dict)


# ── Quirk 4: stale cache annotation ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_research_cached_result_passes_through():
    """Quirk 4: cached:true with old generated_at — data must still be returned."""
    data = {
        "topic": "quantum computing",
        "summary": "Quantum computing is advancing.",
        "sources": ["arxiv.org"],
        "generated_at": "2024-03-15T09:00:00Z",
        "cached": True,
        "cache_age_seconds": 26784000,
    }
    with patch("tools.httpx.AsyncClient", return_value=_client(_resp(data))):
        from tools import research_topic
        result = await research_topic("quantum computing")
    assert result["summary"] == "Quantum computing is advancing."
    assert result["cached"] is True


# ── Quirk 5: inconsistent error shapes ───────────────────────────────────────

def test_parse_error_handles_error_key():
    from tools import _parse_error
    assert _parse_error({"error": "Location not found"}) == "Location not found"


def test_parse_error_handles_fastapi_detail():
    from tools import _parse_error
    data = {"detail": [{"msg": "Field required", "loc": ["query", "location"]}]}
    assert "Field required" in _parse_error(data)


def test_parse_error_handles_message_key():
    from tools import _parse_error
    assert _parse_error({"message": "Something went wrong"}) == "Something went wrong"


# ── Timeout handling ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_research_timeout_returns_error_dict():
    import httpx
    c = AsyncMock()
    c.get.side_effect = httpx.TimeoutException("timed out")
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)

    with patch("tools.httpx.AsyncClient", return_value=c):
        from tools import research_topic
        result = await research_topic("solar energy")
    assert "error" in result
    assert "timed out" in result["error"].lower()