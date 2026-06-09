# Elyos CLI Chat

A production-grade command-line chat interface backed by Claude, with real-time streaming and tool calling.

## Setup

**Requirements:** Python 3.11+

```bash
pip install -e .
```

**Environment variables** (already in `.env`):

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `ELYOS_API_KEY` | Elyos interview API key |
| `ELYOS_BASE_URL` | Elyos API base URL |

## Usage

```bash
python3 main.py
```

```
Elyos Chat — type 'quit' to exit, Ctrl+C to cancel a running request

You: What's the weather in Tokyo?
Assistant: The weather in Tokyo is currently...

You: Research renewable energy trends
Assistant: [spinner: Researching renewable energy trends... (Ctrl+C to cancel)]
Based on research from Nature, ScienceDirect, and arXiv...

You: quit
```

**Controls:**
- `Ctrl+C` — cancel a running request (returns to prompt, partial output preserved)
- `quit` / `exit` / `q` / `Ctrl+D` — exit the app

## Architecture

```
main.py    — REPL loop, Ctrl+C signal handler, env validation
llm.py     — AsyncAnthropic streaming client, tool dispatch loop, history management
tools.py   — get_weather, research_topic, TOOL_DEFINITIONS, error handling
test_api.py — integration tests for all API endpoints and edge cases
```

**Key design decisions:**
- `asyncio.Task` + `SIGINT` handler → real cancellation of in-flight HTTP requests
- `AsyncAnthropic` client → streaming doesn't block the event loop
- Tool errors return `{"error": "..."}` to the LLM rather than crashing
- History stores raw Anthropic message objects (handles multi-tool-call turns correctly)
- `rich` Status spinner runs during slow tool calls so the terminal stays responsive

---

## API Quirks Discovered

All findings from integration tests (`test_api.py`) and live testing.

### Quirk 1: Rate limiting returns HTTP 200, not 429

**Expected:** Rate limits return HTTP 429 with a `Retry-After` header (standard practice).

**Actual:** The API returns **HTTP 200** with this body:
```json
{
  "status": "throttled",
  "message": "Rate limit exceeded. Please wait.",
  "retry_after_seconds": 28,
  "data": null
}
```

**Impact:** Any code that only checks `response.status_code == 200` will silently treat a rate-limit response as success, passing `null` data to the LLM.

**Handling:** `tools.py` checks `data.get("status") == "throttled"` after parsing JSON, extracts `retry_after_seconds`, sleeps (capped at 10s), and retries up to 3 times.

---

### Quirk 2: Weather response schema is non-deterministic

**Expected:** A single weather object with consistent fields.

**Actual:** The API sometimes returns a flat object and sometimes an array:

**Format A (single condition):**
```json
{"location": "London", "temperature_c": 12.3, "condition": "Clear", "humidity": 71}
```

**Format B (multiple conditions):**
```json
{
  "location": "London",
  "conditions": [
    {"temperature_c": 12.3, "condition": "Clear", "humidity": 71},
    {"temperature_c": 11.3, "condition": "light rain", "humidity": 84}
  ],
  "note": "Multiple conditions reported"
}
```

**Impact:** Consumers that always expect `temperature_c` at the top level will fail on Format B.

**Handling:** We pass the raw JSON dict to the LLM as the tool result. Claude handles both schemas gracefully and presents a coherent summary to the user either way. No brittle field extraction in the tool layer.

---

### Quirk 3: Unknown locations return 404 (not a fallback value)

**Actual:** `GET /weather?location=Xyzzyville123` → HTTP 404:
```json
{"error": "Location \"Xyzzyville123\" not found"}
```

**Handling:** `_parse_error()` in `tools.py` extracts the `error` field and returns `{"error": "..."}` to the LLM, which then tells the user the location wasn't found.

---

### Quirk 4: Missing required param returns 422 with FastAPI-style validation error

**Actual:** `GET /weather` (no `location`) → HTTP 422:
```json
{"detail": [{"type": "missing", "loc": ["query", "location"], "msg": "Field required", "input": null}]}
```

**Handling:** `_parse_error()` handles the `detail` array shape, joining all `msg` fields into a readable error string.

---

### Quirk 5: Research response includes `generated_at` timestamp and `sources` list

**Actual response shape:**
```json
{
  "topic": "solar energy",
  "summary": "Research summary for 'solar energy'...",
  "sources": ["nature.com", "sciencedirect.com", "arxiv.org"],
  "generated_at": "2026-06-08T22:43:48.108985+00:00"
}
```

The `sources` array and `generated_at` timestamp are not mentioned in the documentation. Claude uses both naturally in its responses (citing sources, noting recency), which improves answer quality.

---

### Quirk 6: Authentication error is consistent across both endpoints

Both endpoints return HTTP 401 with `{"error": "Invalid or missing API key"}` for bad or missing `X-API-Key`. This is clean and detectable at startup — though we rely on runtime errors rather than a pre-flight auth check.

---

### Quirk 7: Empty string parameter returns 404, not 422

**Actual:** `GET /weather?location=` → HTTP 404:
```json
{"error": "Location \"\" not found"}
```

The API treats an empty string as a valid-but-missing location (passes validation, fails lookup), unlike a missing param which returns 422. The LLM could theoretically call `get_weather(location="")` and receive a 404 rather than a validation error.

**Handling:** Falls through the same 404 path as Quirk 3 — `_parse_error()` surfaces the error string to the LLM.

---

### Quirk 8: Burst concurrency is allowed; throttle is time-window based

5 simultaneous requests all return HTTP 200. The rate limit is not concurrency-based — it triggers on sustained high-frequency calls across time windows, not on parallel bursts within a single session.

---

## Summary

| # | Endpoint | Behavior | Status | Handled by |
|---|---|---|---|---|
| 1 | Both | Rate limit returns 200 (not 429) | 200 | `_check_throttled()` + retry loop |
| 2 | `/weather` | Non-deterministic response schema | 200 | Pass raw JSON to LLM |
| 3 | `/weather` | Unknown location → 404 | 404 | `_parse_error()` |
| 4 | Both | Missing param → 422 FastAPI error | 422 | `_parse_error()` detail handler |
| 5 | `/research` | Undocumented `sources` + `generated_at` | 200 | Pass raw JSON to LLM (beneficial) |
| 6 | Both | Auth errors consistent (wrong = missing) | 401 | `_parse_error()` |
| 7 | `/weather` | Empty string → 404 (not 422) | 404 | `_parse_error()` |
| 8 | Both | Burst concurrency allowed; time-window throttle | — | Documented only |
