"""Firecrawl v2 HTTP client: search, scrape, interact."""

from __future__ import annotations

import time

import httpx

from ..env import get

BASE = "https://api.firecrawl.dev/v2"


class Saturated(RuntimeError):
    """The account is at its concurrency limit — no browser slot is available."""


class Firecrawl:
    def __init__(self, timeout: float = 120.0):
        self._client = httpx.Client(
            base_url=BASE,
            headers={"Authorization": f"Bearer {get('FIRECRAWL_API_KEY')}"},
            timeout=timeout,
        )

    def _call(self, method: str, path: str, body: dict | None = None, retries: int = 2) -> dict:
        for attempt in range(retries + 1):
            resp = self._client.request(method, path, json=body)
            if resp.status_code == 429 and attempt < retries:
                time.sleep(2 * (attempt + 1))  # brief contention, not a deep queue
                continue
            if resp.status_code == 429:
                raise Saturated(resp.text[:300])
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"firecrawl {method} {path} -> {resp.status_code}: {resp.text[:400]}"
                )
            return resp.json()
        raise AssertionError("unreachable")

    def search(self, query: str, limit: int = 8, **opts) -> dict:
        body = {"query": query, "limit": limit}
        body.update({k: v for k, v in opts.items() if v is not None})
        return self._call("POST", "/search", body)

    def scrape(self, url: str, wait_for: int = 0, max_age: int = 172_800_000) -> dict:
        body = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "maxAge": max_age,
        }
        if wait_for:
            body["waitFor"] = wait_for
        return self._call("POST", "/scrape", body)

    def interact(
        self,
        scrape_id: str,
        prompt: str | None = None,
        code: str | None = None,
        language: str = "node",
        timeout: int = 30,
    ) -> dict:
        body: dict = {"timeout": timeout}
        if prompt:
            body["prompt"] = prompt
        if code:
            body["code"] = code
            body["language"] = language
        return self._call("POST", f"/scrape/{scrape_id}/interact", body)

    def interact_stop(self, scrape_id: str) -> dict:
        return self._call("DELETE", f"/scrape/{scrape_id}/interact")
