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

**Run tests:**

```bash
pytest tests/test_api.py -v
```

## Usage

```bash
python3 src/main.py
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
main.py     — REPL loop, Ctrl+C signal handler, env validation
llm.py      — AsyncAnthropic streaming client, tool dispatch loop, history management
tools.py    — get_weather, research_topic, TOOL_DEFINITIONS, error handling
tests/
  test_api.py — tests for all API quirks and edge cases
```

**Key design decisions:**
- `asyncio.Task` + `SIGINT` handler → real cancellation of in-flight HTTP requests
- `AsyncAnthropic` client → streaming doesn't block the event loop
- Tool errors return `{"error": "..."}` to the LLM rather than crashing
- History stores raw Anthropic message objects (handles multi-tool-call turns correctly)
- `rich` Status spinner runs during slow tool calls so the terminal stays responsive

---

## API Quirks Discovered

All findings from probe testing and live integration.

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

**Handling:** We pass the raw JSON dict to the LLM as the tool result. Claude handles both schemas gracefully and presents a coherent summary either way. No brittle field extraction in the tool layer.

---

### Quirk 3: Research returns `{}` empty body on HTTP 200

**Actual:** Some research queries return a valid HTTP 200 with a completely empty JSON body `{}` — no `summary`, no `topic`, nothing.

**Impact:** Any code that does `data["summary"]` will raise `KeyError` silently.

**Handling:** We pass the empty dict to the LLM as-is. Claude surfaces "no data available" to the user rather than crashing.

---

### Quirk 4: Research returns stale cached results

**Actual:** Some responses include undocumented `cached: true` and `cache_age_seconds` fields:
```json
{
  "topic": "quantum computing",
  "summary": "...",
  "cached": true,
  "cache_age_seconds": 26784000,
  "generated_at": "2024-03-15T09:00:00Z"
}
```
`cache_age_seconds: 26784000` = ~310 days old.

**Impact:** The user could receive outdated research without knowing it.

**Handling:** We pass the full response including `cached` and `generated_at` to the LLM. Claude flags the age to the user when relevant.

---

### Quirk 5: Unknown locations return 404 (not a fallback value)

**Actual:** `GET /weather?location=Xyzzyville123` → HTTP 404:
```json
{"error": "Location \"Xyzzyville123\" not found"}
```

**Handling:** `_parse_error()` in `tools.py` extracts the `error` field and returns `{"error": "..."}` to the LLM, which tells the user the location wasn't found.

---

### Quirk 6: Missing required param returns 422 with FastAPI-style validation error

**Actual:** `GET /weather` (no `location`) → HTTP 422:
```json
{"detail": [{"type": "missing", "loc": ["query", "location"], "msg": "Field required", "input": null}]}
```

**Handling:** `_parse_error()` handles the `detail` array shape, joining all `msg` fields into a readable error string.

---

### Quirk 7: Research response includes undocumented `sources` and `generated_at`

**Actual response shape:**
```json
{
  "topic": "solar energy",
  "summary": "...",
  "sources": ["nature.com", "sciencedirect.com", "arxiv.org"],
  "generated_at": "2026-06-08T22:43:48Z"
}
```

These fields are not in the documentation. Claude uses both naturally — citing sources and noting recency — which improves answer quality.

---

### Quirk 8: Empty string parameter returns 404, not 422

**Actual:** `GET /weather?location=` → HTTP 404 (not 422). An empty string passes validation but fails lookup.

**Handling:** Falls through the same 404 path as Quirk 5.

---

### Quirk 9: Burst concurrency is allowed; throttle is time-window based

5 simultaneous requests all return HTTP 200. The rate limit triggers on sustained high-frequency calls across time windows, not on parallel bursts within a single session.

---

### Quirk 10: Authentication errors are consistent across both endpoints

Both endpoints return HTTP 401 with `{"error": "Invalid or missing API key"}` for bad or missing `X-API-Key`.

---

## Summary

| # | Endpoint | Behavior | HTTP | Handled by |
|---|---|---|---|---|
| 1 | Both | Rate limit returns 200 not 429 | 200 | `_check_throttled()` + retry loop |
| 2 | `/weather` | Non-deterministic response schema | 200 | Pass raw JSON to LLM |
| 3 | `/research` | Empty `{}` body on success | 200 | Pass to LLM, no KeyError |
| 4 | `/research` | Stale cached results (undocumented) | 200 | Pass `cached` + `generated_at` to LLM |
| 5 | `/weather` | Unknown location → 404 | 404 | `_parse_error()` |
| 6 | Both | Missing param → 422 FastAPI error | 422 | `_parse_error()` detail handler |
| 7 | `/research` | Undocumented `sources` + `generated_at` | 200 | Pass raw JSON to LLM (beneficial) |
| 8 | `/weather` | Empty string → 404 not 422 | 404 | `_parse_error()` |
| 9 | Both | Burst concurrency allowed; time-window throttle | — | Documented only |
| 10 | Both | Auth errors consistent (401) | 401 | `_parse_error()` |