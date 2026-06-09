"""
API Discovery Script — run once to document Elyos API behaviors.
Findings feed directly into error handling in tools.py.
"""

import asyncio
import json
import os
import sys
import time

import httpx
from dotenv import load_dotenv
load_dotenv()

BASE_URL = os.environ["ELYOS_BASE_URL"]
API_KEY = os.environ["ELYOS_API_KEY"]

HEADERS = {"X-API-Key": API_KEY}


async def probe(client: httpx.AsyncClient, label: str, url: str, **kwargs) -> None:
    print(f"\n{'='*60}")
    print(f"PROBE: {label}")
    print(f"URL:   {url}")
    start = time.monotonic()
    try:
        r = await client.get(url, headers=HEADERS, **kwargs)
        elapsed = time.monotonic() - start
        print(f"Status: {r.status_code}  ({elapsed:.2f}s)")
        print(f"Headers: {dict(r.headers)}")
        print(f"Body (raw): {r.text[:500]}")
        try:
            print(f"Body (json): {json.dumps(r.json(), indent=2)[:800]}")
        except Exception:
            print("Body is not valid JSON")
    except Exception as e:
        elapsed = time.monotonic() - start
        print(f"EXCEPTION after {elapsed:.2f}s: {type(e).__name__}: {e}")


async def main() -> None:
    if not API_KEY:
        print("ERROR: ELYOS_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    timeout = httpx.Timeout(30.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:

        # --- Weather probes ---
        await probe(client, "weather: happy path (London)", f"{BASE_URL}/weather?location=London")
        await probe(client, "weather: happy path (Tokyo)", f"{BASE_URL}/weather?location=Tokyo")
        await probe(client, "weather: empty location", f"{BASE_URL}/weather?location=")
        await probe(client, "weather: no location param", f"{BASE_URL}/weather")
        await probe(client, "weather: special chars", f"{BASE_URL}/weather?location=S%C3%A3o+Paulo")
        await probe(client, "weather: very long string", f"{BASE_URL}/weather?location={'x'*500}")
        await probe(client, "weather: fake city", f"{BASE_URL}/weather?location=Xyzzyville123")
        await probe(client, "weather: numeric", f"{BASE_URL}/weather?location=12345")

        # --- Weather: bad auth ---
        print(f"\n{'='*60}")
        print("PROBE: weather: bad API key")
        r = await client.get(f"{BASE_URL}/weather?location=London", headers={"X-API-Key": "badkey"})
        print(f"Status: {r.status_code}  Body: {r.text[:300]}")

        # --- Weather: no auth ---
        print(f"\n{'='*60}")
        print("PROBE: weather: no API key header")
        r = await client.get(f"{BASE_URL}/weather?location=London")
        print(f"Status: {r.status_code}  Body: {r.text[:300]}")

        # --- Research probes ---
        await probe(client, "research: happy path (solar energy)", f"{BASE_URL}/research?topic=solar+energy")
        await probe(client, "research: empty topic", f"{BASE_URL}/research?topic=")
        await probe(client, "research: no topic param", f"{BASE_URL}/research")
        await probe(client, "research: special chars", f"{BASE_URL}/research?topic=AI+%26+ML")
        await probe(client, "research: very long string", f"{BASE_URL}/research?topic={'y'*500}")

        # --- Concurrent research calls ---
        print(f"\n{'='*60}")
        print("PROBE: concurrent research calls (2 simultaneous)")
        start = time.monotonic()
        results = await asyncio.gather(
            client.get(f"{BASE_URL}/research?topic=quantum+computing", headers=HEADERS),
            client.get(f"{BASE_URL}/research?topic=climate+change", headers=HEADERS),
            return_exceptions=True,
        )
        elapsed = time.monotonic() - start
        print(f"Both completed in {elapsed:.2f}s")
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"  [{i}] EXCEPTION: {r}")
            else:
                print(f"  [{i}] Status: {r.status_code}  Body snippet: {r.text[:200]}")

        # --- Rapid repeated weather calls ---
        print(f"\n{'='*60}")
        print("PROBE: rapid repeated weather calls (5x)")
        tasks = [client.get(f"{BASE_URL}/weather?location=Paris", headers=HEADERS) for _ in range(5)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"  [{i}] EXCEPTION: {r}")
            else:
                print(f"  [{i}] Status: {r.status_code}")

    print(f"\n{'='*60}")
    print("Probe complete. Document findings in README.md API Quirks section.")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    asyncio.run(main())
