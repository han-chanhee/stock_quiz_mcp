"""MarketClient 계약(Protocol) 및 공용 상수.

이 Protocol 시그니처는 계약이다. B(services), D(batch)는 이 인터페이스에만 의존한다.
실 구현(KISClient)과 mock(MockMarketClient)은 동일 Protocol을 만족해야 한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from contracts.schemas import Market, Period, RankingItem, StockSnapshot


@runtime_checkable
class MarketClient(Protocol):
    """시세/랭킹 조회 계약. 반환은 항상 contracts 모델. 원본 dict 노출 금지."""

    async def top_market_cap(self, market: Market, n: int) -> list[StockSnapshot]:
        """시총 상위 n종목 스냅샷 (price_quiz / guess_company 풀 재료)."""
        ...

    async def top_movers(
        self, market: Market, period: Period, direction: str, n: int
    ) -> list[RankingItem]:
        """기간별 등락률 랭킹 상위 n. direction: "up" 상승 / "down" 하락."""
        ...

    async def snapshot(self, ticker: str, market: Market) -> StockSnapshot | None:
        """단일 종목 현재가 스냅샷. 없으면 None."""
        ...


# mock/배치 재현성을 위한 기준 시각. 실 클라이언트는 datetime.now(tz)를 쓴다.
# 절대 고정값이 아니라 "테스트 실행 시각 - 1시간"으로 둔다: server/cache.py의
# STALE_AFTER_HOURS(36) 판정 대상이므로, 값을 고정하면 시간이 지날수록 mock
# 데이터 자체가 stale 판정에 걸려 무관한 테스트가 깨진다(TASK-003에서 실측).
_KST = timezone(timedelta(hours=9))


def _mock_as_of() -> datetime:
    return datetime.now(_KST) - timedelta(hours=1)


MOCK_AS_OF = _mock_as_of()
