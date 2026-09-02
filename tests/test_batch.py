"""모듈 D 테스트: mock 입력 배치 1회 → 산출 JSON 스키마 재파싱, 섹터 풀 구조, as_of."""

from __future__ import annotations

import json
from collections import Counter

import pytest

from batch import DailyBatch, MockReasonProvider
from batch.daily import EmptyOutputError
from clients import MockMarketClient
from contracts.schemas import RankingItem, Reason, StockSnapshot


@pytest.mark.asyncio
async def test_batch_outputs_reparse(tmp_path):
    batch = DailyBatch(
        MockMarketClient(), data_dir=tmp_path, reason_provider=MockReasonProvider()
    )
    await batch.run()

    # 1. top20 재파싱 + as_of 존재
    for fname in ("top20_kr.json", "top20_us.json"):
        arr = json.loads((tmp_path / fname).read_text(encoding="utf-8"))
        snaps = [StockSnapshot.model_validate(x) for x in arr]
        assert snaps and all(s.as_of is not None for s in snaps)

    # 2. movers 재파싱 (기간×시장×방향)
    movers = list(tmp_path.glob("movers_*.json"))
    assert movers, "movers 산출물 없음"
    for f in movers:
        arr = json.loads(f.read_text(encoding="utf-8"))
        items = [RankingItem.model_validate(x) for x in arr]
        assert all(i.rank >= 1 for i in items)

    # 3. sector_top100: 정확히 10섹터, 섹터당 ≤10종목
    pool = json.loads((tmp_path / "sector_top100.json").read_text(encoding="utf-8"))
    snaps = [StockSnapshot.model_validate(x) for x in pool]
    counts = Counter(s.sector for s in snaps)
    assert len(counts) == 10
    assert all(c <= 10 for c in counts.values())
    assert all(s.sector is not None for s in snaps)

    # 4. reasons: 전부 source_url 보유 (Reason 재파싱)
    reasons = json.loads((tmp_path / "reasons.json").read_text(encoding="utf-8"))
    for v in reasons.values():
        r = Reason.model_validate(v)
        assert r.source_url


@pytest.mark.asyncio
async def test_sector_pool_from_curated_universe(tmp_path):
    """sector_universe.json이 있으면 종목별 snapshot으로 섹터 균형 풀을 만든다."""
    univ = [
        {"ticker": "005930", "name": "삼성전자", "sector": "반도체"},
        {"ticker": "035420", "name": "NAVER", "sector": "인터넷게임"},
        {"ticker": "352820", "name": "하이브", "sector": "엔터"},
    ]
    (tmp_path / "sector_universe.json").write_text(
        json.dumps(univ, ensure_ascii=False), encoding="utf-8"
    )
    await DailyBatch(
        MockMarketClient(), data_dir=tmp_path, reason_provider=MockReasonProvider()
    ).run()
    pool = json.loads((tmp_path / "sector_top100.json").read_text(encoding="utf-8"))
    snaps = [StockSnapshot.model_validate(x) for x in pool]
    names = {s.name for s in snaps}
    assert {"삼성전자", "NAVER", "하이브"} <= names
    assert all(s.price > 0 for s in snaps)       # 실(모의)시세 부여
    assert all(s.sector is not None for s in snaps)


@pytest.mark.asyncio
async def test_batch_aliases_autoregister(tmp_path):
    batch = DailyBatch(MockMarketClient(), data_dir=tmp_path)
    await batch.run()
    aliases = json.loads((tmp_path / "aliases.json").read_text(encoding="utf-8"))
    # 신규 종목명이 정규화 키로 자동 등록됨
    assert "삼성전자" in aliases.values()


@pytest.mark.asyncio
async def test_reason_provider_none_when_unseen(tmp_path):
    provider = MockReasonProvider(seed={})  # 아무 근거도 없음
    from contracts.schemas import Market
    assert await provider.fetch("005930", "삼성전자", Market.KR) is None


@pytest.mark.asyncio
async def test_batch_preserves_existing_outputs_when_all_snapshots_fail(tmp_path):
    """시세 전건 실패 시 기존 섹터 풀과 근거 파일을 보존하고 실패를 알린다."""

    class FailingSnapshotClient(MockMarketClient):
        async def top_market_cap(self, market, n):
            raise RuntimeError("시세 조회 실패")

        async def snapshot(self, ticker, market):
            raise RuntimeError("시세 조회 실패")

    class EmptyReasonProvider:
        async def fetch(self, ticker, name, market):
            return None

    universe = [{"ticker": "005930", "name": "삼성전자", "sector": "반도체"}]
    existing_pool = [
        {
            "ticker": "005930",
            "name": "삼성전자",
            "market": "KR",
            "price": 70000,
            "currency": "KRW",
            "change_pct": 0.0,
            "as_of": "2026-01-01T09:00:00+09:00",
            "sector": "반도체",
            "market_cap_rank": 1,
        }
    ]
    existing_reasons = {"005930": {"preserved": True}}
    (tmp_path / "sector_universe.json").write_text(
        json.dumps(universe, ensure_ascii=False), encoding="utf-8"
    )
    sector_path = tmp_path / "sector_top100.json"
    reasons_path = tmp_path / "reasons.json"
    sector_path.write_text(json.dumps(existing_pool), encoding="utf-8")
    reasons_path.write_text(json.dumps(existing_reasons), encoding="utf-8")

    batch = DailyBatch(
        FailingSnapshotClient(),
        data_dir=tmp_path,
        reason_provider=EmptyReasonProvider(),
    )

    with pytest.raises(EmptyOutputError):
        await batch.run()

    assert json.loads(sector_path.read_text(encoding="utf-8")) == existing_pool
    assert json.loads(reasons_path.read_text(encoding="utf-8")) == existing_reasons


@pytest.mark.asyncio
async def test_batch_not_implemented_market_is_not_a_failure(tmp_path):
    """NotImplementedError(예: US 미검증 비활성)는 산출 실패가 아니라 정상 skip이다.

    실 KISClient는 US 조회에서 항상 NotImplementedError를 던진다(US_ENABLED=False).
    이를 산출 실패로 잘못 카운트하면 정상 운영 중인 배치가 매번 실패 종료한다
    (2026-08-15 KR 데이터 갱신 시 실측 — 데이터 유실은 없었으나 exit code 오염).
    """

    class USNotImplementedClient(MockMarketClient):
        async def top_market_cap(self, market, n):
            from contracts.schemas import Market as _Market

            if market == _Market.US:
                raise NotImplementedError("US 시총순위 미검증 — 실 클라이언트에서 비활성")
            return await super().top_market_cap(market, n)

        async def top_movers(self, market, period, direction, n):
            from contracts.schemas import Market as _Market

            if market == _Market.US:
                raise NotImplementedError("US 등락률 순위는 배치 프리캐싱 경로에서 조립")
            return await super().top_movers(market, period, direction, n)

    batch = DailyBatch(
        USNotImplementedClient(), data_dir=tmp_path, reason_provider=MockReasonProvider()
    )
    await batch.run()  # raise하면 안 된다 — US 비활성은 정상 상태

    kr = json.loads((tmp_path / "top20_kr.json").read_text(encoding="utf-8"))
    assert kr, "KR 데이터는 정상 갱신돼야 한다"
    assert not (tmp_path / "top20_us.json").exists()
