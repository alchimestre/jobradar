"""Minimal HTTP helper: one client, bounded retries, no surprises."""

from __future__ import annotations

import httpx


class Fetcher:
    def __init__(self, timeout: int = 25, user_agent: str = "jobradar/0.1"):
        self.client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "*/*"},
            follow_redirects=True,
        )

    def get(self, url: str, tries: int = 2) -> httpx.Response:
        last: Exception | None = None
        for _ in range(tries):
            try:
                response = self.client.get(url)
                if response.status_code >= 500:
                    response.raise_for_status()
                return response
            except httpx.HTTPError as exc:  # transient: retry once
                last = exc
        raise last  # type: ignore[misc]

    def json(self, url: str):
        response = self.get(url)
        response.raise_for_status()
        return response.json()

    def text(self, url: str) -> str:
        response = self.get(url)
        response.raise_for_status()
        return response.text

    def close(self) -> None:
        self.client.close()
