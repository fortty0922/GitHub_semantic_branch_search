import asyncio
import logging
import time

import httpx

API = "https://api.github.com"
log = logging.getLogger("crawler")


class RateLimitedError(Exception):
    pass


class GraphQLError(Exception):
    pass


class BucketLimiter:
    """Paces requests evenly over what is left of the current rate-limit window."""

    def __init__(self, name: str, limit: int, window: int, reserve_ratio: float = 0.01):
        self.name = name
        self.limit = limit
        self.window = window
        self.reserve_ratio = reserve_ratio
        self.remaining = limit
        self.reset = time.time() + window
        self.blocked_until = 0.0
        self.sent = 0
        self._next = 0.0
        self._lock = asyncio.Lock()

    def update(self, remaining: int, reset: float, limit: int | None = None) -> None:
        if limit:
            self.limit = limit
        if reset > self.reset + 1:
            self.remaining = remaining
        else:
            self.remaining = min(self.remaining, remaining)
        self.reset = max(self.reset, reset)

    def update_from_headers(self, headers: httpx.Headers) -> None:
        try:
            self.update(
                int(headers["x-ratelimit-remaining"]),
                float(headers["x-ratelimit-reset"]),
                int(headers.get("x-ratelimit-limit", 0)),
            )
        except (KeyError, ValueError):
            pass

    def interval(self, now: float) -> float:
        if now >= self.reset:
            self.remaining = self.limit
            self.reset = now + self.window
        usable = self.remaining - max(1, int(self.limit * self.reserve_ratio))
        if usable <= 0:
            return self.reset - now + 1
        return (self.reset - now) / usable

    def block(self, until: float) -> None:
        self.blocked_until = max(self.blocked_until, until)

    async def acquire(self) -> None:
        async with self._lock:
            now = time.time()
            wait = max(self._next, self.blocked_until) - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.time()
            self._next = now + self.interval(now)
            self.remaining -= 1
            self.sent += 1


class GitHubClient:
    def __init__(self, token: str, reserve_ratio: float = 0.01, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(
            base_url=API,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "repo-crawler",
            },
            timeout=30,
            follow_redirects=True,
            transport=transport,
        )
        self.limiters = {
            "core": BucketLimiter("core", 5000, 3600, reserve_ratio),
            "search": BucketLimiter("search", 30, 60, reserve_ratio),
            "graphql": BucketLimiter("graphql", 5000, 3600, reserve_ratio),
        }

    async def close(self) -> None:
        await self.http.aclose()

    async def init_limits(self) -> None:
        r = await self.http.get("/rate_limit")
        r.raise_for_status()
        resources = r.json()["resources"]
        for name, limiter in self.limiters.items():
            res = resources[name]
            limiter.limit = res["limit"]
            limiter.remaining = res["remaining"]
            limiter.reset = float(res["reset"])

    async def get(self, bucket: str, path: str, params=None, headers=None) -> httpx.Response:
        return await self.request(bucket, "GET", path, params=params, headers=headers)

    async def graphql(self, query: str) -> dict:
        """Returns `data`; raises GraphQLError on server errors/timeouts so the caller can shrink the batch."""
        r = await self.request("graphql", "POST", "/graphql", retry_5xx=False, json={"query": query})
        if r.status_code != 200:
            raise GraphQLError(f"HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            raise GraphQLError(f"truncated/invalid JSON ({len(r.content)} bytes)") from None
        if body.get("data") is None:
            raise GraphQLError(str(body.get("errors"))[:300])
        return body["data"]

    async def request(self, bucket: str, method: str, path: str, max_retries: int = 6,
                      retry_5xx: bool = True, **kwargs) -> httpx.Response:
        limiter = self.limiters[bucket]
        for attempt in range(max_retries):
            await limiter.acquire()
            try:
                r = await self.http.request(method, path, **kwargs)
            except httpx.TransportError as e:
                log.warning("%s %s transport error: %s", bucket, path, e)
                await asyncio.sleep(2**attempt)
                continue
            limiter.update_from_headers(r.headers)
            if self._is_rate_limited(r):
                until = self._retry_at(r, attempt)
                log.warning("%s rate limited (%s), pausing %.0fs", bucket, r.status_code, until - time.time())
                limiter.block(until)
                continue
            if r.status_code >= 500 and retry_5xx:
                await asyncio.sleep(2**attempt)
                continue
            return r
        raise RateLimitedError(f"{bucket} {path}: gave up after {max_retries} attempts")

    @staticmethod
    def _is_rate_limited(r: httpx.Response) -> bool:
        if r.status_code == 429:
            return True
        if r.status_code == 200:
            if r.headers.get("x-ratelimit-remaining") != "0" or r.url.path != "/graphql":
                return False
            try:
                return any(e.get("type") == "RATE_LIMITED" for e in r.json().get("errors") or [])
            except ValueError:
                return False
        if r.status_code != 403:
            return False
        return (
            r.headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in r.headers
            or "rate limit" in r.text.lower()
        )

    @staticmethod
    def _retry_at(r: httpx.Response, attempt: int) -> float:
        now = time.time()
        if "retry-after" in r.headers:
            return now + float(r.headers["retry-after"])
        if r.headers.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in r.headers:
            return float(r.headers["x-ratelimit-reset"]) + 1
        return now + 60 * 2**attempt
