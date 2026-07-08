"""모듈 A 테스트: MockMarketClient 스키마 유효성 + TokenBucket 한도 준수."""

from __future__ import annotations

import pytest

from clients import MockMarketClient, TokenBucket
from contracts.schemas import Market, Period, RankingItem, StockSnapshot


@pytest.mark.asyncio
async def test_top_market_cap_returns_valid_snapshots():
    c = MockMarketClient()
    snaps = await c.top_market_cap(Market.KR, 20)
    assert len(snaps) == 20
    assert all(isinstance(s, StockSnapshot) for s in snaps)
    # 시총 순위 오름차순
    ranks = [s.market_cap_rank for s in snaps]
    assert ranks == sorted(ranks)


@pytest.mark.asyncio
async def test_top_movers_direction_ordering():
    c = MockMarketClient()
    up = await c.top_movers(Market.KR, Period.WEEK, "up", 5)
    down = await c.top_movers(Market.KR, Period.WEEK, "down", 5)
    assert all(isinstance(x, RankingItem) for x in up + down)
    up_pcts = [x.snapshot.change_pct for x in up]
    down_pcts = [x.snapshot.change_pct for x in down]
    # 상승 랭킹은 내림차순, 하락 랭킹은 오름차순(더 많이 하락한 것이 앞)
    assert up_pcts == sorted(up_pcts, reverse=True)
    assert down_pcts == sorted(down_pcts)
    assert up_pcts[0] > 0 and down_pcts[0] < 0
    # 랭크는 1..n
    assert [x.rank for x in up] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_snapshot_found_and_missing():
    c = MockMarketClient()
    s = await c.snapshot("005930", Market.KR)
    assert s is not None and s.name == "삼성전자"
    assert await c.snapshot("999999", Market.KR) is None


@pytest.mark.asyncio
async def test_bad_direction_raises():
    c = MockMarketClient()
    with pytest.raises(ValueError):
        await c.top_movers(Market.KR, Period.TODAY, "sideways", 5)


def test_token_bucket_respects_limit_with_mock_clock():
    now = {"t": 0.0}
    bucket = TokenBucket(rate=5.0, capacity=5.0, clock=lambda: now["t"])
    # 버스트 5개 소비 성공, 6번째 실패
    assert sum(bucket.try_acquire() for _ in range(5)) == 5
    assert bucket.try_acquire() is False
    # 0.2초 경과 → 토큰 1개 리필
    now["t"] = 0.2
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False
    # 1초 더 경과 → capacity 상한(5)까지만 리필
    now["t"] = 10.0
    assert sum(bucket.try_acquire() for _ in range(10)) == 5


@pytest.mark.asyncio
async def test_token_bucket_async_acquire_returns():
    bucket = TokenBucket(rate=1000.0)
    await bucket.acquire()  # 충분한 rate라 즉시 반환
