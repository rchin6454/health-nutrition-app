"""The per-model Groq budget: all four limits, Groq's headers, cooldowns and seeding."""

import pytest

from app.llm.rate_limit import (
    DAY_S,
    BudgetExceeded,
    Limits,
    ModelBudget,
    estimate_tokens,
    parse_duration,
)

FREE_TIER = Limits(rpm=30, rpd=1000, tpm=8000, tpd=200_000)


class FakeClock:
    def __init__(self) -> None:
        self.now = 10_000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def _budget(clock: FakeClock, limits: Limits = FREE_TIER) -> ModelBudget:
    return ModelBudget(limits, clock=clock, sleep=clock.sleep)


async def test_calls_within_limits_go_straight_through(clock: FakeClock) -> None:
    budget = _budget(clock)
    for _ in range(3):
        await budget.acquire(2000, max_wait_s=0)
    assert clock.slept == []
    assert budget.usage() == {
        "requests_minute": 3,
        "requests_day": 3,
        "tokens_minute": 6000,
        "tokens_day": 6000,
    }


async def test_tokens_per_minute_waits_for_the_window_when_it_is_short(clock: FakeClock) -> None:
    budget = _budget(clock)
    await budget.acquire(3000, max_wait_s=0)
    clock.now += 20
    await budget.acquire(3000, max_wait_s=0)
    clock.now += 5
    # 6000 used; 3000 more needs the first call (25 s ago) to leave the window: 35 s.
    await budget.acquire(3000, max_wait_s=40)
    assert clock.slept == [pytest.approx(35)]


async def test_tokens_per_minute_refuses_when_the_wait_is_too_long(clock: FakeClock) -> None:
    budget = _budget(clock)
    await budget.acquire(6000, max_wait_s=0)
    with pytest.raises(BudgetExceeded) as info:
        await budget.acquire(3000, max_wait_s=8)
    assert info.value.limit == "tpm"
    assert info.value.retry_after_s == pytest.approx(60)
    assert clock.slept == []  # refused at once, nothing was sent
    assert budget.usage()["requests_minute"] == 1


async def test_settling_to_real_usage_frees_the_estimate(clock: FakeClock) -> None:
    budget = _budget(clock)
    reservation = await budget.acquire(6000, max_wait_s=0)
    budget.settle(reservation, 1800)
    await budget.acquire(6000, max_wait_s=0)
    assert clock.slept == []


async def test_requests_per_minute(clock: FakeClock) -> None:
    budget = _budget(clock)
    for _ in range(30):
        await budget.acquire(10, max_wait_s=0)
        clock.now += 1
    with pytest.raises(BudgetExceeded) as info:
        await budget.acquire(10, max_wait_s=0)
    assert info.value.limit == "rpm"
    assert info.value.retry_after_s == pytest.approx(30)  # the first call leaves the window


async def test_requests_per_day(clock: FakeClock) -> None:
    budget = _budget(clock)
    budget.seed([(3600.0 + i, 1) for i in range(1000)])  # 1000 calls, 1-1.3 hours ago
    with pytest.raises(BudgetExceeded) as info:
        await budget.acquire(10, max_wait_s=8)
    assert info.value.limit == "rpd"
    assert info.value.retry_after_s == pytest.approx(DAY_S - 3600 - 999)


async def test_tokens_per_day(clock: FakeClock) -> None:
    budget = _budget(clock)
    budget.seed([(7200.0, 99_000), (3600.0, 99_000)])
    await budget.acquire(1500, max_wait_s=0)
    with pytest.raises(BudgetExceeded) as info:
        await budget.acquire(1500, max_wait_s=8)
    assert info.value.limit == "tpd"
    assert info.value.retry_after_s == pytest.approx(DAY_S - 7200)

    clock.now += DAY_S - 7200  # the oldest seeded call leaves the day window
    await budget.acquire(1500, max_wait_s=0)


async def test_a_call_larger_than_the_minute_limit_can_never_fit(clock: FakeClock) -> None:
    with pytest.raises(BudgetExceeded) as info:
        await _budget(clock).acquire(8001, max_wait_s=60)
    assert info.value.limit == "too_large"
    assert info.value.retry_after_s is None


async def test_groq_headers_override_local_counts(clock: FakeClock) -> None:
    # Another process (or a restart) used most of this minute's tokens.
    budget = _budget(clock)
    budget.observe_headers(
        {"x-ratelimit-remaining-tokens": "900", "x-ratelimit-reset-tokens": "7.5s"}
    )
    await budget.acquire(800, max_wait_s=0)  # fits, and uses Groq's remaining tokens
    await budget.acquire(800, max_wait_s=8)  # 100 left: waits for Groq's window to reset
    assert clock.slept == [pytest.approx(7.5)]


async def test_no_requests_left_today_per_groq(clock: FakeClock) -> None:
    budget = _budget(clock)
    budget.observe_headers(
        {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2m59.5s"}
    )
    with pytest.raises(BudgetExceeded) as info:
        await budget.acquire(100, max_wait_s=8)
    assert info.value.limit == "rpd"
    assert info.value.retry_after_s == pytest.approx(179.5)


async def test_cooldown_after_a_429(clock: FakeClock) -> None:
    budget = _budget(clock)
    budget.cool_down(20)
    with pytest.raises(BudgetExceeded) as info:
        await budget.acquire(100, max_wait_s=8)
    assert info.value.limit == "cooldown"
    clock.now += 20
    await budget.acquire(100, max_wait_s=0)


async def test_seed_ignores_calls_older_than_a_day(clock: FakeClock) -> None:
    budget = _budget(clock)
    budget.seed([(DAY_S + 1, 150_000), (30.0, 2000)])
    assert budget.usage() == {
        "requests_minute": 1,
        "requests_day": 1,
        "tokens_minute": 2000,
        "tokens_day": 2000,
    }


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("7.66s", 7.66),
        ("2m59.56s", 179.56),
        ("1h2m3s", 3723.0),
        ("250ms", 0.25),
        ("12", 12.0),
        ("", None),
        (None, None),
        ("soon", None),
    ],
)
def test_parse_duration(value: str | None, seconds: float | None) -> None:
    assert parse_duration(value) == (pytest.approx(seconds) if seconds is not None else None)


def test_estimate_is_on_the_high_side() -> None:
    # Measured: ~6,300 prompt characters were 1,650 real prompt tokens.
    assert estimate_tokens(6300, 800) >= 1650 + 800
