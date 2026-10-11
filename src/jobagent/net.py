"""Polite async HTTP for discovery.

curl_cffi impersonates Chrome's TLS/JA3 stack. This is not optional: Workday's edge returns
406 with an empty body to plain HTTP clients (verified during planning), and several ATS
boards sit behind Cloudflare.

Politeness is built in rather than bolted on: a global concurrency cap, a per-host cap,
inter-request jitter, and retry with backoff on 429/5xx.
"""

from __future__ import annotations

import asyncio
import json as _json
import random
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass
class Resp:
    status: int
    url: str
    text: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        return _json.loads(self.text)

    def snippet(self, n: int = 200) -> str:
        return self.text[:n].replace("\n", " ")


class HttpClient:
    def __init__(
        self,
        *,
        impersonate: str = "chrome",
        timeout: float = 30.0,
        global_concurrency: int = 16,
        per_host_concurrency: int = 2,
        jitter: tuple[float, float] = (0.15, 0.5),
        retries: int = 2,
    ) -> None:
        self._session = AsyncSession(impersonate=impersonate, timeout=timeout)
        self._global = asyncio.Semaphore(global_concurrency)
        self._per_host_limit = per_host_concurrency
        self._host_sems: dict[str, asyncio.Semaphore] = {}
        self._jitter = jitter
        self._retries = retries
        self.calls = 0

    async def __aenter__(self) -> HttpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        try:
            await self._session.close()
        except Exception:
            pass

    def _host_sem(self, host: str) -> asyncio.Semaphore:
        if host not in self._host_sems:
            self._host_sems[host] = asyncio.Semaphore(self._per_host_limit)
        return self._host_sems[host]

    async def request(self, method: str, url: str, **kw: Any) -> Resp:
        host = urlparse(url).netloc
        headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
        headers.update(kw.pop("headers", {}) or {})
        last_exc: Exception | None = None
        async with self._global, self._host_sem(host):
            await asyncio.sleep(random.uniform(*self._jitter))
            for attempt in range(self._retries + 1):
                try:
                    r = await self._session.request(method, url, headers=headers, **kw)
                except Exception as exc:  # connection/TLS/timeout
                    last_exc = exc
                    if attempt >= self._retries:
                        raise
                    await asyncio.sleep(2 ** attempt + random.random())
                    continue
                self.calls += 1
                if r.status_code in RETRY_STATUS and attempt < self._retries:
                    await asyncio.sleep(2 ** attempt + random.random())
                    continue
                return Resp(r.status_code, str(r.url), r.text)
        raise last_exc if last_exc else RuntimeError("unreachable")

    async def get(self, url: str, **kw: Any) -> Resp:
        return await self.request("GET", url, **kw)

    async def get_json(self, url: str, **kw: Any) -> Resp:
        kw.setdefault("headers", {})["Accept"] = "application/json"
        return await self.request("GET", url, **kw)

    async def post_json(self, url: str, payload: dict[str, Any], **kw: Any) -> Resp:
        headers = kw.pop("headers", {}) or {}
        headers["Accept"] = "application/json"
        headers.setdefault("Content-Type", "application/json")
        return await self.request("POST", url, headers=headers, json=payload, **kw)
