"""원인 팩트 프리캐싱 제공자.

- Reason은 source_url 없이 생성 불가(contracts에서 구조적 차단 — 환각 방지).
- 링크(source_url)를 못 얻으면 Reason을 만들지 않고 None을 반환한다.
- 런타임에는 이 코드가 호출되지 않는다. 오직 배치(daily.py)에서만 사용한다.
"""

from __future__ import annotations

import asyncio
import os
import re
from html import unescape
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
_FEATURE_KEYWORDS = (
    ("HBM", "HBM"),
    ("D램", "메모리"),
    ("DRAM", "메모리"),
    ("낸드", "메모리"),
    ("반도체", "반도체"),
    ("파운드리", "파운드리"),
    ("AI", "AI"),
    ("인공지능", "AI"),
    ("배터리", "2차전지"),
    ("2차전지", "2차전지"),
    ("전기차", "전기차"),
    ("바이오", "바이오"),
    ("신약", "신약"),
    ("임상", "임상"),
    ("조선", "조선"),
    ("수주", "수주"),
    ("방산", "방산"),
    ("원전", "원전"),
    ("로봇", "로봇"),
    ("플랫폼", "플랫폼"),
    ("커머스", "커머스"),
    ("게임", "게임"),
    ("엔터", "엔터"),
    ("콘텐츠", "콘텐츠"),
    ("배당", "배당"),
    ("자사주", "자사주"),
    ("영업이익", "영업이익"),
    ("실적", "실적"),
    ("매출", "매출"),
)


def _is_relevant(title: str) -> bool:
    if any(w in title for w in _ADVISORY):
        return False
    return any(w in title for w in _RELEVANT)


def _company_key(name: str) -> str:
    return name.replace(" ", "")[:3]


def _mentions_company(text: str, name: str) -> bool:
    key = _company_key(name)
    return bool(key and key in text.replace(" ", ""))


def _feature_summary(texts: list[str]) -> str | None:
    joined = " ".join(texts)
    found: list[str] = []
    for needle, label in _FEATURE_KEYWORDS:
        if needle in joined and label not in found:
            found.append(label)
        if len(found) >= 3:
            break
    if not found:
        return None
    return "특징: " + ", ".join(found)


def _clean_search_text(s: str) -> str:
    text = re.sub(r"</?b>", "", s)
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    return text


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
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id or os.environ.get("NAVER_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("NAVER_CLIENT_SECRET", "")
        self._timeout = timeout
        self._transport = transport

    @staticmethod
    def _strip_tags(s: str) -> str:
        # 네이버 응답의 <b> 태그/엔티티 정리
        return _clean_search_text(s)

    async def fetch(self, ticker: str, name: str, market: Market) -> Reason | None:
        headers = {
            "X-Naver-Client-Id": self._client_id,
            "X-Naver-Client-Secret": self._client_secret,
        }
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            async def fetch_query(query: str) -> list[dict]:
                resp = await client.get(
                    self._ENDPOINT,
                    headers=headers,
                    params={"query": query, "display": 10, "sort": "sim"},
                )
                if resp.status_code != 200:
                    return []
                return resp.json().get("items", [])

            results = await asyncio.gather(
                fetch_query(f"{name} 주가"),
                fetch_query(f"{name} 실적 사업"),
            )
        items = [item for result in results for item in result]
        # 종목 특정성: 제목에 종목명(앞 3글자)이 들어가야 함. 다른 회사 기사 배제.
        feature_sources: list[str] = []
        best: tuple[str, str, datetime] | None = None
        for item in items:
            url = item.get("originallink") or item.get("link")
            if not url:  # 링크 없으면 근거 불충분 → 건너뜀
                continue
            title = self._strip_tags(item.get("title", ""))
            description = self._strip_tags(item.get("description", ""))
            if not title or not _is_relevant(title):
                continue  # 무관/권유성 제목은 재료로 안 씀
            searchable = f"{title} {description}"
            if not _mentions_company(searchable, name):
                continue  # 해당 종목이 제목에 없으면 배제(시장 전반 기사 등)
            feature_sources.append(searchable)
            if best is None:
                try:
                    published = datetime.strptime(
                        item.get("pubDate", ""), "%a, %d %b %Y %H:%M:%S %z"
                    )
                except ValueError:
                    published = datetime.now(timezone.utc)
                best = (title, url, published)
        if best is not None:
            title, url, published = best
            feature = _feature_summary(feature_sources)
            text = f"{title} · {feature}" if feature else title
            return Reason(
                ticker=ticker, text=text, source_url=url, published_at=published
            )
        # 관련 재료를 못 찾음 → None (런타임에서 "특별한 재료 확인 안 됨")
        return None
