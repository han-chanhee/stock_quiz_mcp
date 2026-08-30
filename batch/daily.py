"""모듈 D: 일일 데이터 갱신 배치.

매 영업일 18시 실행(Docker 동일 이미지, 다른 커맨드). 산출물은 전부 batch/data/*.json.
런타임 뉴스/시세 호출은 여기서만 발생하고, 서버 요청 경로에는 존재하지 않는다.

산출:
  1. top20_kr.json / top20_us.json        (price_quiz 풀, StockSnapshot 리스트)
  2. movers_{market}_{period}_{dir}.json   (등락률 랭킹, RankingItem 리스트)
  3. sector_top100.json                    (guess_company 풀, StockSnapshot 리스트)
  4. aliases.json                          (별칭 시드 + 신규 종목명 자동 등록)
  5. reasons.json                          (원인 팩트, ticker -> Reason. source_url 필수)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from contracts.schemas import (
    Market,
    Period,
    RankingItem,
    Reason,
    Sector,
    StockSnapshot,
)

from clients.base import MarketClient

from .reasons import MockReasonProvider, ReasonProvider

_DATA_DIR = Path(__file__).parent / "data"

_ALL_PERIODS = [
    Period.TODAY,
    Period.YESTERDAY,
    Period.WEEK,
    Period.MONTH,
    Period.YEAR,
]
_MARKETS = [Market.KR, Market.US]


class EmptyOutputError(RuntimeError):
    """빈 배치 산출물이 기존 파일을 덮어쓰려 할 때 발생한다."""


def _load_sector_map(data_dir: Path) -> dict[str, str]:
    path = data_dir / "sector_map.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _apply_sector(snap: StockSnapshot, sector_map: dict[str, str]) -> StockSnapshot:
    """실 클라이언트가 섹터를 안 주면 sector_map으로 부여(애매하면 미분류 유지)."""
    if snap.sector is not None:
        return snap
    raw = sector_map.get(snap.ticker)
    if raw is None:
        return snap
    try:
        return snap.model_copy(update={"sector": Sector(raw)})
    except ValueError:
        return snap  # 매핑표에 없는 값이면 그대로


def _dump(path: Path, models: list, allow_empty: bool = False) -> None:
    if not models and not allow_empty:
        raise EmptyOutputError(f"{path.name} 산출물이 비어 있음")
    payload = [m.model_dump(mode="json") for m in models]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class DailyBatch:
    def __init__(
        self,
        client: MarketClient,
        data_dir: Path | None = None,
        reason_provider: ReasonProvider | None = None,
        top_n: int = 20,
        movers_n: int = 5,
        sector_top: int = 10,
        reason_concurrency: int = 8,
    ) -> None:
        self._client = client
        self._dir = data_dir or _DATA_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._reasons = reason_provider or MockReasonProvider()
        self._top_n = top_n
        self._movers_n = movers_n
        self._sector_top = sector_top
        self._reason_concurrency = max(1, reason_concurrency)
        self._sector_map = _load_sector_map(self._dir)
        self._output_failures: list[str] = []

    def _record_failure(self, output: str) -> None:
        self._output_failures.append(output)

    # ── 1. price_quiz 풀 ─────────────────────────────────────

    async def _build_top20(self) -> dict[Market, list[StockSnapshot]]:
        result: dict[Market, list[StockSnapshot]] = {}
        for market in _MARKETS:
            try:
                snaps = await self._client.top_market_cap(market, self._top_n)
            except NotImplementedError:
                # 실 클라이언트 미지원 시장(예: US_ENABLED=False)은 정상 skip —
                # _build_movers와 동일 정책. 산출 실패로 카운트하지 않는다.
                continue
            except Exception as exc:
                # 한 시장 실패가 전체 배치를 무너뜨리지 않게 격리(기존 파일 유지)
                print(f"[batch] top_market_cap {market.value} 건너뜀: {exc!r}")
                self._record_failure(f"top20_{market.value}")
                continue
            snaps = [_apply_sector(s, self._sector_map) for s in snaps]
            fname = "top20_kr.json" if market == Market.KR else "top20_us.json"
            try:
                _dump(self._dir / fname, snaps)
            except EmptyOutputError:
                print(f"[batch] {fname} 산출 0건 — 기존 파일 유지")
                self._record_failure(fname)
                continue
            result[market] = snaps
        return result

    # ── 2. 등락률 랭킹 ───────────────────────────────────────

    async def _build_movers(self) -> None:
        for market in _MARKETS:
            for period in _ALL_PERIODS:
                for direction in ("up", "down"):
                    try:
                        items = await self._client.top_movers(
                            market, period, direction, self._movers_n
                        )
                    except NotImplementedError:
                        # 실 클라이언트 미지원 조합(예: US 등락률)은 건너뜀
                        continue
                    except Exception as exc:
                        # 그 외 오류(엔드포인트/한도)도 격리 — 기존 파일 유지
                        print(
                            f"[batch] movers {market.value}/{period.value}/"
                            f"{direction} 건너뜀: {exc!r}"
                        )
                        self._record_failure(fname := f"movers_{market.value}_{period.value}_{direction}.json")
                        continue
                    items = [
                        RankingItem(
                            rank=it.rank,
                            snapshot=_apply_sector(it.snapshot, self._sector_map),
                            period=it.period,
                        )
                        for it in items
                    ]
                    fname = f"movers_{market.value}_{period.value}_{direction}.json"
                    try:
                        _dump(self._dir / fname, items)
                    except EmptyOutputError:
                        print(f"[batch] {fname} 산출 0건 — 기존 파일 유지")
                        self._record_failure(fname)

    # ── 3. guess_company 풀 ──────────────────────────────────

    async def _build_sector_pool(self) -> list[StockSnapshot]:
        """섹터 균형 풀 구성.

        sector_universe.json(큐레이션: ticker/name/sector)이 있으면 종목별 실시세를
        snapshot()으로 붙여 섹터 균형 풀(섹터당 최대 sector_top)을 만든다.
        없으면 시총 상위 랭킹 + sector_map 태깅으로 폴백(시총 상위는 섹터가 편중됨).
        """
        universe = self._load_sector_universe()
        if universe:
            by_sector = await self._build_pool_from_universe(universe)
        else:
            wide = await self._client.top_market_cap(Market.KR, 200)
            wide = [_apply_sector(s, self._sector_map) for s in wide]
            by_sector = {}
            for snap in wide:
                if snap.sector is not None:
                    by_sector.setdefault(snap.sector, []).append(snap)

        pool: list[StockSnapshot] = []
        for sector in Sector:
            group = by_sector.get(sector, [])
            group.sort(key=lambda s: (s.market_cap_rank or 10**9))
            pool.extend(group[: self._sector_top])  # 섹터당 최대 sector_top
        path = self._dir / "sector_top100.json"
        try:
            _dump(path, pool)
        except EmptyOutputError:
            print("[batch] sector_top100 산출 0건 — 기존 파일 유지")
            self._record_failure("sector_top100.json")
            if not path.exists():
                return []
            existing = json.loads(path.read_text(encoding="utf-8"))
            return [StockSnapshot.model_validate(item) for item in existing]
        return pool

    def _load_sector_universe(self) -> list[dict]:
        path = self._dir / "sector_universe.json"
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    async def _build_pool_from_universe(
        self, universe: list[dict]
    ) -> dict[Sector, list[StockSnapshot]]:
        # 시총순위(상위 30)에서 랭크를 얻어 큐레이션 종목에 부여(있는 것만)
        rank_by_ticker: dict[str, int | None] = {}
        try:
            top = await self._client.top_market_cap(Market.KR, 30)
            rank_by_ticker = {s.ticker: s.market_cap_rank for s in top}
        except Exception as exc:
            print(f"[batch] 시총 랭크 조회 실패(랭크 생략): {exc!r}")

        by_sector: dict[Sector, list[StockSnapshot]] = {}
        for row in universe:
            ticker, name, raw_sector = row["ticker"], row["name"], row["sector"]
            try:
                sector = Sector(raw_sector)
            except ValueError:
                continue
            try:
                snap = await self._client.snapshot(ticker, Market.KR)
            except Exception as exc:
                print(f"[batch] snapshot {ticker}({name}) 실패, 건너뜀: {exc!r}")
                continue
            if snap is None or snap.price <= 0:
                continue  # 유효 시세 없으면 제외
            snap = snap.model_copy(
                update={
                    "name": name,  # inquire-price엔 이름이 없어 큐레이션명으로 확정
                    "sector": sector,
                    "market_cap_rank": rank_by_ticker.get(ticker),
                }
            )
            by_sector.setdefault(sector, []).append(snap)
        return by_sector

    # ── 4. 별칭 자동 등록 ────────────────────────────────────

    def _update_aliases(self, all_snaps: list[StockSnapshot]) -> None:
        path = self._dir / "aliases.json"
        if not all_snaps:
            print("[batch] aliases 산출 0건 — 기존 파일 유지")
            self._record_failure("aliases.json")
            return
        aliases: dict[str, str] = {}
        if path.exists():
            with path.open(encoding="utf-8") as f:
                aliases = json.load(f)
        # 신규 종목: 정규화된 표준명 -> 표준명 자동 등록(오타/약칭은 수동 시드 유지)
        for snap in all_snaps:
            norm = "".join(snap.name.split()).casefold()
            aliases.setdefault(norm, snap.name)
        path.write_text(
            json.dumps(aliases, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── 5. 원인 프리캐싱 ─────────────────────────────────────

    async def _build_reasons(self, all_snaps: list[StockSnapshot]) -> None:
        seen: set[str] = set()
        unique_snaps: list[StockSnapshot] = []
        for snap in all_snaps:
            if snap.ticker in seen:
                continue
            seen.add(snap.ticker)
            unique_snaps.append(snap)

        semaphore = asyncio.Semaphore(self._reason_concurrency)

        async def fetch_one(snap: StockSnapshot) -> tuple[str, Reason | None]:
            async with semaphore:
                try:
                    reason = await self._reasons.fetch(snap.ticker, snap.name, snap.market)
                except Exception as exc:
                    print(f"[batch] reason {snap.ticker}({snap.name}) 실패: {exc!r}")
                    return snap.ticker, None
                return snap.ticker, reason

        results = await asyncio.gather(*(fetch_one(snap) for snap in unique_snaps))

        reasons: dict[str, dict] = {}
        for ticker, reason in results:
            if reason is None:
                continue  # 근거 없으면 저장 안 함 → 런타임 "특별한 재료 확인 안 됨"
            reasons[ticker] = reason.model_dump(mode="json")
        if not reasons:
            print("[batch] reasons 산출 0건 — 기존 파일 유지")
            if all_snaps:
                self._record_failure("reasons.json")
            return
        (self._dir / "reasons.json").write_text(
            json.dumps(reasons, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── 오케스트레이션 ───────────────────────────────────────

    async def run(self) -> None:
        self._output_failures = []
        top20 = await self._build_top20()
        await self._build_movers()
        sector_pool = await self._build_sector_pool()

        # 별칭/원인은 전체 풀(top20 + 섹터100) 대상
        all_snaps: list[StockSnapshot] = []
        for snaps in top20.values():
            all_snaps.extend(snaps)
        all_snaps.extend(sector_pool)

        self._update_aliases(all_snaps)
        await self._build_reasons(all_snaps)
        if self._output_failures:
            failed = ", ".join(self._output_failures)
            raise EmptyOutputError(f"배치 산출 실패: {failed}")
