"""KISClient — 한국투자증권 OpenAPI 비동기 래퍼 (실 구현).

- 접근토큰 발급/자동 갱신 (유효기간 내 재사용)
- 토큰 버킷으로 초당 호출 한도 준수
- 지수 백오프 재시도
- 반환은 항상 contracts 모델. 원본 dict는 밖으로 새지 않는다.

주의: 엔드포인트/TR_ID/필드 경로는 KIS 문서 기준으로 작성했으며,
실 응답과 대조하는 스모크 실행에서 미세 조정이 필요할 수 있다(clients CLAUDE.md).
테스트는 실 키 없이 MockMarketClient로 통과한다.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import httpx

from contracts.schemas import Market, Period, RankingItem, StockSnapshot

from .ratelimit import TokenBucket

_KST = timezone(timedelta(hours=9))

# KIS 기간 코드 매핑 (등락률 순위 조회 파라미터)
_PERIOD_KIS = {
    Period.TODAY: "0",
    Period.YESTERDAY: "1",
    Period.WEEK: "2",
    Period.MONTH: "3",
    Period.YEAR: "4",
}


class KISAuthError(RuntimeError):
    """토큰 발급 실패."""


class KISClient:
    """MarketClient Protocol 실 구현."""

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        base_url: str = "https://openapi.koreainvestment.com:9443",
        rate_per_sec: float = 15.0,
        max_retries: int = 3,
        timeout: float = 5.0,
    ) -> None:
        self._app_key = app_key or os.environ.get("KIS_APP_KEY", "")
        self._app_secret = app_secret or os.environ.get("KIS_APP_SECRET", "")
        self._base_url = base_url.rstrip("/")
        self._bucket = TokenBucket(rate=rate_per_sec)
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        self._token: str | None = None
        self._token_expires: datetime | None = None
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> KISClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # ── 토큰 관리 ────────────────────────────────────────────

    async def _ensure_token(self) -> str:
        now = datetime.now(_KST)
        if self._token and self._token_expires and now < self._token_expires:
            return self._token
        async with self._token_lock:
            # 락 획득 사이 다른 코루틴이 갱신했을 수 있음
            now = datetime.now(_KST)
            if self._token and self._token_expires and now < self._token_expires:
                return self._token
            resp = await self._client.post(
                "/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._app_key,
                    "appsecret": self._app_secret,
                },
            )
            if resp.status_code != 200:
                raise KISAuthError(f"토큰 발급 실패: HTTP {resp.status_code}")
            data = resp.json()
            token = data.get("access_token")
            if not token:
                raise KISAuthError("토큰 응답에 access_token 없음")
            expires_sec = int(data.get("expires_in", 86400))
            self._token = token
            # 만료 1분 전에 갱신하도록 여유
            self._token_expires = now + timedelta(seconds=expires_sec - 60)
            return token

    # ── 공통 요청 (백오프 재시도) ─────────────────────────────

    async def _get(self, path: str, tr_id: str, params: dict) -> dict:
        token = await self._ensure_token()
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            await self._bucket.acquire()
            try:
                resp = await self._client.get(path, headers=headers, params=params)
                if resp.status_code == 200:
                    return resp.json()
                # 429/5xx는 재시도 대상
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, httpx.TransportError) as exc:
                last_exc = exc
                await asyncio.sleep(2**attempt * 0.25)  # 0.25, 0.5, 1.0s
        raise RuntimeError(f"KIS 요청 실패({path}): {last_exc}")

    # ── MarketClient 구현 ────────────────────────────────────

    async def top_market_cap(self, market: Market, n: int) -> list[StockSnapshot]:
        if market == Market.KR:
            data = await self._get(
                "/uapi/domestic-stock/v1/ranking/market-cap",
                tr_id="FHPST01740000",
                params={
                    "fid_cond_mrkt_div_code": "J",
                    "fid_cond_scr_div_code": "20174",
                    "fid_div_cls_code": "0",
                    "fid_input_iscd": "0000",
                    "fid_trgt_cls_code": "0",
                    "fid_trgt_exls_cls_code": "0",
                    "fid_input_price_1": "",
                    "fid_input_price_2": "",  # 상한가: 필수(누락 시 rt_cd 2). 실 스모크 확인
                    "fid_vol_cnt": "",
                },
            )
            rows = data.get("output", [])[:n]
            return [self._kr_snapshot(r, Period.TODAY) for r in rows]
        # US: 해외 시총순위는 미검증(실 스모크 결과 inquire-search가 시총순 정렬을 하지
        # 않고 페니주가 상위로 섞여 나옴). 검증 전 잘못된 랭킹을 서비스하지 않는다.
        # → 실 US는 비활성. mock(MockMarketClient)은 US를 정상 제공하므로 테스트는 통과.
        # TODO(US): 해외 시총순위 엔드포인트/파라미터 확정 후 활성화.
        raise NotImplementedError("US 시총순위 미검증 — 실 클라이언트에서 비활성")

    async def top_movers(
        self, market: Market, period: Period, direction: str, n: int
    ) -> list[RankingItem]:
        if direction not in ("up", "down"):
            raise ValueError("direction은 'up'|'down'")
        gubun = "0" if direction == "up" else "1"  # 상승/하락
        if market == Market.KR:
            data = await self._get(
                "/uapi/domestic-stock/v1/ranking/fluctuation",
                tr_id="FHPST01700000",
                params={
                    "fid_cond_mrkt_div_code": "J",
                    "fid_cond_scr_div_code": "20170",
                    "fid_input_iscd": "0000",
                    "fid_rank_sort_cls_code": gubun,
                    "fid_input_cnt_1": _PERIOD_KIS[period],
                    "fid_prc_cls_code": "0",
                    "fid_input_price_1": "",
                    "fid_input_price_2": "",
                    "fid_vol_cnt": "",
                    "fid_trgt_cls_code": "0",
                    "fid_trgt_exls_cls_code": "0",
                    "fid_div_cls_code": "0",
                    "fid_rsfl_rate1": "",
                    "fid_rsfl_rate2": "",
                },
            )
            rows = data.get("output", [])[:n]
            return [
                RankingItem(rank=i, snapshot=self._kr_snapshot(r, period), period=period)
                for i, r in enumerate(rows, start=1)
            ]
        raise NotImplementedError("US 등락률 순위는 배치 프리캐싱 경로에서 조립")

    async def snapshot(self, ticker: str, market: Market) -> StockSnapshot | None:
        if market == Market.KR:
            data = await self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                tr_id="FHKST01010100",
                params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker},
            )
            out = data.get("output")
            if not out:
                return None
            return self._kr_snapshot(out, Period.TODAY, ticker=ticker)
        # US 개별 시세는 EXCD(거래소) 확정이 필요해 미검증 — 실 클라이언트에서 비활성.
        # TODO(US): 거래소 매핑(NAS/NYS/AMS) + 필드 확정 후 활성화.
        raise NotImplementedError("US 개별 시세 미검증 — 실 클라이언트에서 비활성")

    # ── 응답 → 스키마 변환 ───────────────────────────────────

    def _kr_snapshot(
        self, row: dict, period: Period, ticker: str | None = None
    ) -> StockSnapshot:
        return StockSnapshot(
            ticker=ticker or row.get("mksc_shrn_iscd") or row.get("stck_shrn_iscd", ""),
            # inquire-price 응답엔 종목명이 없어 ticker로 폴백(랭킹 응답엔 hts_kor_isnm 존재).
            # 실 스모크 확인: 시총/등락률 랭킹은 hts_kor_isnm 제공, 개별 현재가는 미제공.
            name=row.get("hts_kor_isnm") or (ticker or ""),
            market=Market.KR,
            sector=None,  # 섹터는 batch sector_map에서 부여
            price=float(row.get("stck_prpr", 0) or 0),
            change_pct=float(row.get("prdy_ctrt", 0) or 0),
            market_cap_rank=int(row["data_rank"]) if row.get("data_rank") else None,
            as_of=datetime.now(_KST),
        )

    def _us_snapshot(
        self, row: dict, period: Period, ticker: str | None = None
    ) -> StockSnapshot:
        return StockSnapshot(
            ticker=ticker or row.get("symb", ""),
            name=row.get("name") or row.get("knam", ""),
            market=Market.US,
            sector=None,
            price=float(row.get("last") or row.get("ovrs_nmix_prpr", 0) or 0),
            change_pct=float(row.get("rate") or row.get("t_rate", 0) or 0),
            market_cap_rank=int(row["rank"]) if row.get("rank") else None,
            as_of=datetime.now(_KST),
        )
