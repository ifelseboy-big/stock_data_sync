from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import monotonic, sleep
from typing import Protocol


class RequestRateLimiter(Protocol):
    def acquire(self) -> float:
        """Reserve one request and return seconds spent waiting."""


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    requests: int
    window_seconds: float

    def __post_init__(self) -> None:
        if self.requests <= 0:
            raise ValueError("requests must be greater than zero")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero")


class SmoothRateLimiter:
    """Thread-safe leaky-bucket limiter that spreads calls evenly across a window."""

    def __init__(
        self,
        policy: RateLimitPolicy,
        *,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.policy = policy
        self._interval = policy.window_seconds / policy.requests
        self._clock = clock
        self._sleep = sleeper
        self._lock = Lock()
        self._next_request_at = 0.0

    def acquire(self) -> float:
        with self._lock:
            now = self._clock()
            request_at = max(now, self._next_request_at)
            self._next_request_at = request_at + self._interval

        wait_seconds = max(0.0, request_at - now)
        if wait_seconds:
            self._sleep(wait_seconds)
        return wait_seconds


class RateLimiterRegistry:
    """Share one limiter per provider across every task thread in this process."""

    def __init__(self) -> None:
        self._limiters: dict[str, SmoothRateLimiter] = {}
        self._lock = Lock()

    def get(self, name: str, policy: RateLimitPolicy) -> SmoothRateLimiter:
        with self._lock:
            limiter = self._limiters.get(name)
            if limiter is None:
                limiter = SmoothRateLimiter(policy)
                self._limiters[name] = limiter
            elif limiter.policy != policy:
                raise ValueError(f"Rate limiter policy for {name!r} is already configured")
            return limiter


rate_limiter_registry = RateLimiterRegistry()
