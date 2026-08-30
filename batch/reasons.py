"""원인 팩트 프리캐싱 제공자.

- Reason은 source_url 없이 생성 불가(contracts에서 구조적 차단 — 환각 방지).
- 링크(source_url)를 못 얻으면 Reason을 만들지 않고 None을 반환한다.
- 런타임에는 이 코드가 호출되지 않는다. 오직 배치(daily.py)에서만 사용한다.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Protocol

import httpx

from contracts.schemas import Market, Reason

# 주가/종목 관련성 키워드 — 뉴스 제목에 이 중 하나라도 있어야 "재료"로 인정.
# (없으면 무관한 헤드라인이므로 "특별한 재료 확인 안 됨" 처리)
_RELEVANT = (
    "주가", "상승", "하락", "급등", "급락", "강세", "약세", "실적", "목표주",
    "신고가", "52주", "어닝", "흑자", "적자", "배당", "수주", "계약", "증설",
    "출시", "합병", "인수", "분할", "자사주", "공매도", "외국인", "기관",
    "반등", "조정", "호재", "악재", "매출", "영업이익", "신제품", "리콜",
)
# 매수/매도 권유성 표현 — 제목에 있으면 노출 거부(루트 규칙 8: 권유 문장 금지).
_ADVISORY = ("매수", "매도", "추천", "사라", "팔아", "담아", "손절", "익절", "비중확대", "비중축소")


def _is_relevant(title: str) -> bool:
    if any(w in title for w in _ADVISORY):
        return False
    return any(w in title for w in _RELEVANT)


class ReasonProvider(Protocol):
    async def fetch(self, ticker: str, name: str, market: Market) -> Reason | None:
        ...


class MockReasonProvider:
    """테스트/오프라인용. 시드된 종목만 Reason, 나머지는 None."""

    def __init__(self, seed: dict[str, tuple[str, str]] | None = None) -> None:
        # ticker -> (fact_text, source_url)
        self._seed = seed or {
            "329180": (
                "조선 슈퍼사이클 기대에 수주잔고 증가 소식",
                "https://finance.example.com/news/329180",
            ),
            "000660": (
                "HBM 수요 강세로 실적 개선 전망 보도",
                "https://finance.example.com/news/000660",
            ),
            "NVDA": (
                "데이터센터 GPU 수요 지속 보도",
                "https://finance.example.com/news/NVDA",
            ),
        }
        self._published = datetime(2026, 7, 7, 8, 0, tzinfo=timezone.utc)

    async def fetch(self, ticker: str, name: str, market: Market) -> Reason | None:
        hit = self._seed.get(ticker)
        if hit is None:
            return None  # 근거 없음 → 저장 안 함 (런타임에서 "특별한 재료 확인 안 됨")
        text, url = hit
        return Reason(
            ticker=ticker, text=text, source_url=url, published_at=self._published
        )


class NaverReasonProvider:
    """네이버 뉴스 검색 API로 종목당 최신 1건 수집(실 구현).

    링크가 없으면 Reason을 만들지 않는다(source_url 필수 — 환각 차단).
    """

    _ENDPOINT = "https://openapi.naver.com/v1/search/news.json"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._client_id = client_id or os.environ.get("NAVER_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("NAVER_CLIENT_SECRET", "")
        self._timeout = timeout

    @staticmethod
    def _strip_tags(s: str) -> str:
        # 네이버 응답의 <b> 태그/엔티티 정리
        return (
            s.replace("<b>", "")
            .replace("</b>", "")
            .replace("&quot;", '"')
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .strip()
        )

    async def fetch(self, ticker: str, name: str, market: Market) -> Reason | None:
        headers = {
            "X-Naver-Client-Id": self._client_id,
            "X-Naver-Client-Secret": self._client_secret,
        }
        # 주가 관련 뉴스로 검색을 좁히고, 후보 여러 개 중 관련 있는 첫 건만 채택.
        params = {"query": f"{name} 주가", "display": 10, "sort": "sim"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self._ENDPOINT, headers=headers, params=params)
            if resp.status_code != 200:
                return None
            items = resp.json().get("items", [])
        # 종목 특정성: 제목에 종목명(앞 3글자)이 들어가야 함. 다른 회사 기사 배제.
        key = name.replace(" ", "")[:3]
        for item in items:
            url = item.get("originallink") or item.get("link")
            if not url:  # 링크 없으면 근거 불충분 → 건너뜀
                continue
            title = self._strip_tags(item.get("title", ""))
            if not title or not _is_relevant(title):
                continue  # 무관/권유성 제목은 재료로 안 씀
            if key and key not in title.replace(" ", ""):
                continue  # 해당 종목이 제목에 없으면 배제(시장 전반 기사 등)
            try:
                published = datetime.strptime(
                    item.get("pubDate", ""), "%a, %d %b %Y %H:%M:%S %z"
                )
            except ValueError:
                published = datetime.now(timezone.utc)
            return Reason(
                ticker=ticker, text=title, source_url=url, published_at=published
            )
        # 관련 재료를 못 찾음 → None (런타임에서 "특별한 재료 확인 안 됨")
        return None
