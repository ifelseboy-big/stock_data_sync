import pytest

from app.integrations.rate_limit import RateLimitPolicy, SmoothRateLimiter


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_smooth_rate_limiter_spaces_requests_evenly() -> None:
    fake_time = FakeTime()
    limiter = SmoothRateLimiter(
        RateLimitPolicy(requests=2, window_seconds=1),
        clock=fake_time.monotonic,
        sleeper=fake_time.sleep,
    )

    waits = [limiter.acquire(), limiter.acquire(), limiter.acquire()]

    assert waits == [0.0, 0.5, 0.5]
    assert fake_time.sleeps == [0.5, 0.5]


def test_480_request_budget_never_places_more_than_480_requests_in_one_minute() -> None:
    fake_time = FakeTime()
    limiter = SmoothRateLimiter(
        RateLimitPolicy(requests=480, window_seconds=60),
        clock=fake_time.monotonic,
        sleeper=fake_time.sleep,
    )

    request_times: list[float] = []
    for _ in range(481):
        limiter.acquire()
        request_times.append(fake_time.now)

    assert request_times[479] == pytest.approx(59.875)
    assert request_times[480] == pytest.approx(60.0)
