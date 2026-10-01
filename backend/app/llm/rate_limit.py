"""Per-model Groq budget: paces or refuses calls so the app stays inside Groq's rate limits.

Groq limits each model separately: requests per minute and per day, tokens per minute and per
day. Before every call, `ModelBudget.acquire` checks all four:

- If the call fits, a reservation is recorded and the call goes ahead.
- If only a per-minute window is full and it frees up within `max_wait`, the call waits.
  That is pacing *before* a call, never a retry of a failed one (R7).
- Otherwise it raises `BudgetExceeded` without calling Groq. The caller records it as a failure
  and tells the user honestly when to try again.

Local counts are corrected from Groq's `x-ratelimit-*` response headers (remaining tokens this
minute, remaining requests today). After a 429, its `retry-after` puts the model in cooldown so
later requests fail fast instead of hitting Groq again. At startup the day window is re-filled
from stored usage (`seed`), so a redeploy does not forget today's spend.
"""

import asyncio
import math
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

MINUTE_S = 60.0
DAY_S = 86_400.0

LimitName = Literal["rpm", "rpd", "tpm", "tpd", "cooldown", "too_large"]


@dataclass(frozen=True)
class Limits:
    rpm: int
    rpd: int
    tpm: int
    tpd: int


class BudgetExceeded(Exception):
    """The call would break a Groq limit; no request was sent."""

    def __init__(self, limit: LimitName, retry_after_s: float | None) -> None:
        wait = "never" if retry_after_s is None else f"{retry_after_s:.1f}s"
        super().__init__(f"{limit} budget exhausted; fits again in {wait}")
        self.limit: LimitName = limit
        self.retry_after_s = retry_after_s  # None: this call can never fit


@dataclass
class Reservation:
    at: float  # clock time the call was admitted
    tokens: int  # estimated at first, replaced by the real total once known


@dataclass
class _ServerView:
    remaining: int
    until: float  # clock time the value stops being meaningful (the window resets)


class ModelBudget:
    def __init__(
        self,
        limits: Limits,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.limits = limits
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._calls: deque[Reservation] = deque()  # admitted calls from the last day, oldest first
        self._cooldown_until = 0.0
        self._server_tokens: _ServerView | None = None  # TPM, from x-ratelimit-*-tokens
        self._server_requests: _ServerView | None = None  # RPD, from x-ratelimit-*-requests

    async def acquire(self, tokens: int, *, max_wait_s: float) -> Reservation:
        deadline = self._clock() + max_wait_s
        while True:
            async with self._lock:
                now = self._clock()
                self._prune(now)
                wait, limit = self._wait_needed(now, tokens)
                if wait <= 0:
                    reservation = Reservation(now, tokens)
                    self._calls.append(reservation)
                    self._consume_server_view(now, tokens)
                    return reservation
            if math.isinf(wait):
                raise BudgetExceeded(limit, None)
            if now + wait > deadline:
                raise BudgetExceeded(limit, wait)
            await self._sleep(wait)

    def settle(self, reservation: Reservation, actual_tokens: int) -> None:
        """Replace the estimate with the tokens Groq actually counted."""
        reservation.tokens = actual_tokens

    def observe_headers(self, headers: Mapping[str, str]) -> None:
        """Adopt Groq's own view of the remaining per-minute tokens and daily requests."""
        now = self._clock()
        tokens = _server_view(headers, "tokens", now)
        if tokens is not None:
            self._server_tokens = tokens
        requests = _server_view(headers, "requests", now)
        if requests is not None:
            self._server_requests = requests

    def cool_down(self, retry_after_s: float) -> None:
        """After a 429: refuse calls to this model until Groq says it will accept them."""
        self._cooldown_until = max(self._cooldown_until, self._clock() + retry_after_s)

    def seed(self, past_calls: Iterable[tuple[float, int]]) -> None:
        """Add calls made before this process started, as `(age_s, tokens)` pairs."""
        now = self._clock()
        seeded = [Reservation(now - age, t) for age, t in past_calls if age < DAY_S]
        self._calls = deque(sorted([*self._calls, *seeded], key=lambda r: r.at))

    def usage(self) -> dict[str, int]:
        """Current use of each window, for logs and health checks."""
        now = self._clock()
        self._prune(now)
        minute = [r for r in self._calls if r.at > now - MINUTE_S]
        return {
            "requests_minute": len(minute),
            "requests_day": len(self._calls),
            "tokens_minute": sum(r.tokens for r in minute),
            "tokens_day": sum(r.tokens for r in self._calls),
        }

    # --- internals ---

    def _prune(self, now: float) -> None:
        while self._calls and self._calls[0].at <= now - DAY_S:
            self._calls.popleft()

    def _wait_needed(self, now: float, tokens: int) -> tuple[float, LimitName]:
        """Seconds until a call of `tokens` fits (0: now, inf: never), and the binding limit."""
        if tokens > self.limits.tpm:
            return math.inf, "too_large"
        minute = [r for r in self._calls if r.at > now - MINUTE_S]
        day = list(self._calls)
        waits: list[tuple[float, LimitName]] = [
            (self._cooldown_until - now, "cooldown"),
            (_wait_for_requests(minute, self.limits.rpm, MINUTE_S, now), "rpm"),
            (_wait_for_requests(day, self.limits.rpd, DAY_S, now), "rpd"),
            (_wait_for_tokens(minute, tokens, self.limits.tpm, MINUTE_S, now), "tpm"),
            (_wait_for_tokens(day, tokens, self.limits.tpd, DAY_S, now), "tpd"),
        ]
        server_tokens = self._server_tokens
        if server_tokens and now < server_tokens.until and server_tokens.remaining < tokens:
            waits.append((server_tokens.until - now, "tpm"))
        server_requests = self._server_requests
        if server_requests and now < server_requests.until and server_requests.remaining < 1:
            waits.append((server_requests.until - now, "rpd"))
        return max(waits, key=lambda w: w[0])

    def _consume_server_view(self, now: float, tokens: int) -> None:
        if self._server_tokens and now < self._server_tokens.until:
            self._server_tokens.remaining -= tokens
        if self._server_requests and now < self._server_requests.until:
            self._server_requests.remaining -= 1


def _wait_for_requests(calls: list[Reservation], limit: int, window: float, now: float) -> float:
    excess = len(calls) + 1 - limit  # how many of the oldest calls must leave the window
    if excess <= 0:
        return 0.0
    if limit <= 0:
        return math.inf
    return calls[excess - 1].at + window - now


def _wait_for_tokens(
    calls: list[Reservation], tokens: int, limit: int, window: float, now: float
) -> float:
    excess = sum(r.tokens for r in calls) + tokens - limit
    if excess <= 0:
        return 0.0
    freed = 0
    for r in calls:
        freed += r.tokens
        if freed >= excess:
            return r.at + window - now
    return math.inf  # only reachable if tokens > limit, which the caller rejects first


_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT_S = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(value: str | None) -> float | None:
    """Groq's reset durations ("7.66s", "2m59.56s", "1h2m", "250ms") or plain seconds."""
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    parts = _DURATION_PART.findall(value)
    if not parts or "".join(n + u for n, u in parts) != value:
        return None
    return sum(float(n) * _UNIT_S[u] for n, u in parts)


def _server_view(headers: Mapping[str, str], kind: str, now: float) -> _ServerView | None:
    remaining = headers.get(f"x-ratelimit-remaining-{kind}")
    reset = parse_duration(headers.get(f"x-ratelimit-reset-{kind}"))
    if remaining is None or reset is None:
        return None
    try:
        return _ServerView(remaining=int(float(remaining)), until=now + reset)
    except ValueError:
        return None


# Characters per token for the pre-call estimate. Measured ~3.8 for these prompts; lower is safer.
CHARS_PER_TOKEN = 3.5


def estimate_tokens(prompt_chars: int, completion_reserve: int) -> int:
    """A deliberately high guess of a call's total tokens, settled to the real count afterwards."""
    return math.ceil(prompt_chars / CHARS_PER_TOKEN) + completion_reserve
