"""서버 TTL/기동 캐시. batch/data/*.json을 기동 시 전수 검증 후 메모리 적재.

- 검증 실패 파일이 하나라도 있으면 기동 중단(루트 규칙 13 — 썩은 데이터 방지).
- 당일 파일이 없으면(배치 미실행) 있는 파일로 기동하되 stale=True 유지.
- 사용자 요청 경로는 이 캐시에서만 조립한다. 외부 API 호출 없음(성능 100ms 규칙).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contracts.schemas import (
    Market,
    Period,
    RankingItem,
    Reason,
    Sector,
    StockSnapshot,
)


class DataValidationError(RuntimeError):
    """산출 JSON이 스키마 검증에 실패. 기동을 중단시킨다."""


def _normalize_key(s: str) -> str:
    return "".join(s.split()).casefold()


class QuizCache:
    """배치 산출물을 메모리에 적재한 읽기 전용 조회 계층."""

    # 배치는 매 영업일 18시에 실행된다. 정상 최대 경과 24시간에 주말 여유를 두되,
    # 금요일 배치가 월요일 오전까지 불필요하게 경고되지 않도록 36시간을 기준으로 삼는다.
    STALE_AFTER_HOURS: int = 36

    def __init__(self, data_dir: Path) -> None:
        self._dir = Path(data_dir)
        self._top20: dict[Market, list[StockSnapshot]] = {}
        self._movers: dict[tuple[Market, Period, str], list[RankingItem]] = {}
        self._sector_pool: list[StockSnapshot] = []
        self._aliases: dict[str, str] = {}
        self._reasons: dict[str, Reason] = {}
        self._stale = False
        self._data_as_of: datetime | None = None

    # ── 로드 ─────────────────────────────────────────────────

    def _read(self, name: str, optional: bool = False) -> object | None:
        """파일 로드. 부재 시 None. optional=False면 stale로 표시한다.

        optional=True는 '지금은 서비스 안 하는' 데이터(예: 비활성 US)에 쓴다 —
        부재가 정상이므로 stale로 오탐하지 않는다.
        """
        path = self._dir / name
        if not path.exists():
            if not optional:
                self._stale = True  # 서비스 대상 파일 부재 → 낡은 상태로 표시
            return None
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def _track_as_of(self, snap: StockSnapshot) -> None:
        as_of = snap.as_of
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        if self._data_as_of is None or as_of > self._data_as_of:
            self._data_as_of = as_of

    def load(self) -> QuizCache:
        """전 파일 검증 적재. 스키마 오류 시 DataValidationError로 기동 중단."""
        try:
            # 1. price_quiz 풀
            for market, fname in (
                (Market.KR, "top20_kr.json"),
                (Market.US, "top20_us.json"),
            ):
                # US는 현재 비활성(handlers.US_ENABLED=False) — 부재를 stale로 보지 않음
                raw = self._read(fname, optional=market == Market.US)
                if raw is None:
                    continue
                snaps = [StockSnapshot.model_validate(x) for x in raw]
                for s in snaps:
                    self._track_as_of(s)
                self._top20[market] = snaps

            # 2. 등락률 랭킹
            for market in (Market.KR, Market.US):
                for period in Period:
                    for direction in ("up", "down"):
                        fname = (
                            f"movers_{market.value}_{period.value}_{direction}.json"
                        )
                        raw = self._read(fname, optional=market == Market.US)
                        if raw is None:
                            continue
                        items = [RankingItem.model_validate(x) for x in raw]
                        for it in items:
                            self._track_as_of(it.snapshot)
                        self._movers[(market, period, direction)] = items

            # 3. guess_company 풀
            raw = self._read("sector_top100.json")
            if raw is not None:
                pool = [StockSnapshot.model_validate(x) for x in raw]
                for s in pool:
                    self._track_as_of(s)
                self._sector_pool = pool

            # 4. 별칭 테이블 (키 정규화)
            raw = self._read("aliases.json")
            if raw is not None:
                self._aliases = {
                    _normalize_key(k): v for k, v in raw.items()
                }

            # 5. 원인 프리캐싱
            raw = self._read("reasons.json")
            if raw is not None:
                self._reasons = {
                    tk: Reason.model_validate(v) for tk, v in raw.items()
                }
        except Exception as exc:  # 검증 실패 = 즉시 기동 중단
            raise DataValidationError(f"배치 데이터 검증 실패: {exc}") from exc
        return self

    # ── 조회 ─────────────────────────────────────────────────

    def top20(self, market: Market) -> list[StockSnapshot]:
        return self._top20.get(market, [])

    def movers(
        self, market: Market, period: Period, direction: str
    ) -> list[RankingItem]:
        return self._movers.get((market, period, direction), [])

    def sector_pool(self, sector: Sector | None = None) -> list[StockSnapshot]:
        if sector is None:
            return self._sector_pool
        return [s for s in self._sector_pool if s.sector == sector]

    def aliases(self) -> dict[str, str]:
        return self._aliases

    def reason(self, ticker: str) -> Reason | None:
        return self._reasons.get(ticker)

    def update_movers(
        self, market: Market, period: Period, direction: str, items: list[RankingItem]
    ) -> None:
        """리프레셔가 today 랭킹을 장중 갱신할 때 호출."""
        self._movers[(market, period, direction)] = items

    def update_top20(self, market: Market, snaps: list[StockSnapshot]) -> None:
        """리프레셔가 시총 상위 종목을 장중 갱신할 때 호출."""
        self._top20[market] = snaps

    @property
    def stale(self) -> bool:
        if self._stale or self._data_as_of is None:
            return True
        age = datetime.now(timezone.utc) - self._data_as_of
        return age > timedelta(hours=self.STALE_AFTER_HOURS)

    @property
    def data_as_of(self) -> datetime | None:
        return self._data_as_of
