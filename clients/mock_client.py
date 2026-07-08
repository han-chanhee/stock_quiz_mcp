"""MockMarketClient — mocks/ 픽스처 기반 오프라인 클라이언트.

실 API 키 없이 B/D/E 모듈이 개발·테스트할 수 있게 한다.
유니버스 JSON(mock 내부 포맷)을 읽어 계약 모델(StockSnapshot/RankingItem)로 변환한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from contracts.schemas import Market, Period, RankingItem, Sector, StockSnapshot

from .base import MOCK_AS_OF

_MOCKS_DIR = Path(__file__).parent / "mocks"


def _load_universe(market: Market) -> list[dict]:
    fname = "universe_kr.json" if market == Market.KR else "universe_us.json"
    with (_MOCKS_DIR / fname).open(encoding="utf-8") as f:
        return json.load(f)


class MockMarketClient:
    """MarketClient Protocol 구현. 결정론적(고정 as_of)이라 테스트에 적합."""

    def __init__(self, as_of: datetime | None = None) -> None:
        self._as_of = as_of or MOCK_AS_OF
        self._universe: dict[Market, list[dict]] = {
            Market.KR: _load_universe(Market.KR),
            Market.US: _load_universe(Market.US),
        }

    def _to_snapshot(self, row: dict, market: Market, period: Period) -> StockSnapshot:
        sector = Sector(row["sector"]) if row.get("sector") else None
        return StockSnapshot(
            ticker=row["ticker"],
            name=row["name"],
            market=market,
            sector=sector,
            price=row["price"],
            change_pct=row["changes"].get(period.value, 0.0),
            market_cap_rank=row.get("market_cap_rank"),
            as_of=self._as_of,
        )

    async def top_market_cap(self, market: Market, n: int) -> list[StockSnapshot]:
        rows = sorted(self._universe[market], key=lambda r: r["market_cap_rank"])
        return [
            self._to_snapshot(r, market, Period.TODAY) for r in rows[: max(0, n)]
        ]

    async def top_movers(
        self, market: Market, period: Period, direction: str, n: int
    ) -> list[RankingItem]:
        if direction not in ("up", "down"):
            raise ValueError(f"direction은 'up'|'down'이어야 함: {direction!r}")
        rows = self._universe[market]
        reverse = direction == "up"
        ranked = sorted(
            rows, key=lambda r: r["changes"].get(period.value, 0.0), reverse=reverse
        )
        items: list[RankingItem] = []
        for rank, row in enumerate(ranked[: max(0, n)], start=1):
            items.append(
                RankingItem(
                    rank=rank,
                    snapshot=self._to_snapshot(row, market, period),
                    period=period,
                )
            )
        return items

    async def snapshot(self, ticker: str, market: Market) -> StockSnapshot | None:
        for row in self._universe[market]:
            if row["ticker"] == ticker:
                return self._to_snapshot(row, market, Period.TODAY)
        return None
