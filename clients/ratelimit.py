"""토큰 버킷 rate limiter. KIS 초당 호출 한도 준수용.

클럭을 주입 가능하게 만들어 시간 mock 테스트가 가능하다(clients CLAUDE.md 완료 정의).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable


class TokenBucket:
    """초당 rate개 토큰을 채우는 버킷. capacity는 순간 버스트 상한."""

    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate는 양수여야 함")
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate
        self._clock = clock
        self._tokens = self.capacity
        self._last = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last = now

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """비차단 획득. 토큰이 있으면 소비하고 True, 없으면 False."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    async def acquire(self, tokens: float = 1.0) -> None:
        """토큰이 확보될 때까지 대기 후 소비."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                await asyncio.sleep(deficit / self.rate)
