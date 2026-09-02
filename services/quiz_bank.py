"""출제 4종 생성. 정답은 QuizState에만 담고 QuizQuestion에는 절대 넣지 않는다.

힌트는 출제 시점에 전 단계를 precompute해 QuizState.hints_precomputed에 저장한다
(채점 경로의 문자열 연산 제거 — 루트 규칙 12).

데이터(풀/랭킹)는 caller(server)가 캐시에서 조립해 넘긴다. 이 모듈은 요청 경로에서
외부 API를 호출하지 않는다(성능 100ms 규칙).
"""

from __future__ import annotations

import random
import secrets
from datetime import datetime, timedelta, timezone

from contracts.schemas import (
    Hint,
    Market,
    Period,
    QuizQuestion,
    QuizState,
    QuizType,
    RankingItem,
    Sector,
    StockSnapshot,
)

from .hangul import chosung_hint, first_letter_hint, first_two_hint

_KST = timezone(timedelta(hours=9))

_PERIOD_LABEL = {
    Period.TODAY: "오늘",
    Period.YESTERDAY: "어제",
    Period.WEEK: "최근 일주일",
    Period.MONTH: "최근 한 달",
    Period.YEAR: "최근 1년",
}

_MARKET_LABEL = {Market.KR: "코스피/코스닥", Market.US: "미국 증시"}
_FORCED_COMPANY_TICKER = "035720"
_FORCED_COMPANY_NAME = "카카오"


def _new_id() -> str:
    """추측 불가 quiz_id(store와 동일 규격). services는 store를 import하지 않으므로 자체 생성."""
    return secrets.token_urlsafe(8)


def _fmt_price(snap: StockSnapshot) -> str:
    if snap.market == Market.KR:
        return f"{snap.price:,.0f}원"
    return f"${snap.price:,.2f}"


def _fmt_pct(pct: float) -> str:
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.2f}%"


class QuizBank:
    """출제 4종 팩토리. rng/clock 주입으로 테스트 결정론 확보."""

    def __init__(
        self,
        rng: random.Random | None = None,
        clock: "callable" = None,
    ) -> None:
        self._rng = rng or random.Random()
        self._clock = clock or (lambda: datetime.now(_KST))

    # ── 힌트 precompute ──────────────────────────────────────

    def _name_hints(self, snap: StockSnapshot) -> list[Hint]:
        """이름 맞추기 힌트 2단계.

        한글 이름: 초성 → 첫 글자.
        한글이 없는 이름(라틴, 예: NAVER/한국 상장 외국계): 초성이 전부 마스킹돼
        정보가 0이므로 첫 글자 → 앞 2글자로 대체(무용한 '_____' 방지).
        """
        has_hangul = any("가" <= c <= "힣" for c in snap.name)
        if has_hangul:
            return [
                Hint(kind="chosung", text=chosung_hint(snap.name)),
                Hint(kind="first_letter", text=first_letter_hint(snap.name)),
            ]
        return [
            Hint(kind="first_letter", text=first_letter_hint(snap.name)),
            Hint(kind="first_letter", text=first_two_hint(snap.name)),
        ]

    # ── 1. price_quiz ────────────────────────────────────────

    def price_quiz(self, pool: list[StockSnapshot]) -> tuple[QuizQuestion, QuizState]:
        if not pool:
            raise ValueError("price_quiz 풀이 비어 있음")
        answer = self._rng.choice(pool)
        quiz_id = _new_id()
        digits = len(str(int(answer.price)))  # 자릿수 힌트(막막함 방지, 정답 유출 없음)
        question = QuizQuestion(
            quiz_id=quiz_id,
            quiz_type=QuizType.PRICE,
            question_md=(
                f"**{answer.name}**의 현재 주가는 얼마일까요? "
                f"({digits}자리 숫자, 1만원 단위 반올림 · 예: 8 = 8만원)"
            ),
            hint_policy="updown",
        )
        state = QuizState(
            quiz_id=quiz_id,
            quiz_type=QuizType.PRICE,
            answer=answer,
            hints_precomputed=[],  # UP/DOWN은 채점 시 제출값 비교로 생성
            created_at=self._clock(),
        )
        return question, state

    # ── 2·3. top_gainers / top_losers ────────────────────────

    def movers_quiz(
        self, ranking: list[RankingItem], quiz_type: QuizType, market: Market
    ) -> tuple[QuizQuestion, QuizState]:
        if quiz_type not in (QuizType.GAINER, QuizType.LOSER):
            raise ValueError("movers_quiz는 GAINER|LOSER만")
        if not ranking:
            raise ValueError("movers 랭킹이 비어 있음")
        item = self._rng.choice(ranking)
        answer = item.snapshot
        quiz_id = _new_id()
        verb = "많이 오른" if quiz_type == QuizType.GAINER else "많이 떨어진"
        period_label = _PERIOD_LABEL.get(item.period, "")
        market_label = _MARKET_LABEL[market]
        question_md = (
            f"{period_label} {market_label}에서 "
            f"가장 {verb} 종목은? ({_fmt_pct(answer.change_pct)})"
        )
        hint_policy = "chosung" if market == Market.KR else "first_letter"
        question = QuizQuestion(
            quiz_id=quiz_id,
            quiz_type=quiz_type,
            question_md=question_md,
            hint_policy=hint_policy,
        )
        state = QuizState(
            quiz_id=quiz_id,
            quiz_type=quiz_type,
            answer=answer,
            hints_precomputed=self._name_hints(answer),
            created_at=self._clock(),
        )
        return question, state

    # ── 4. guess_company ─────────────────────────────────────

    def company_quiz(
        self, pool: list[StockSnapshot], sector: Sector | None = None
    ) -> tuple[QuizQuestion, QuizState]:
        forced = next(
            (
                s for s in pool
                if s.ticker == _FORCED_COMPANY_TICKER or s.name == _FORCED_COMPANY_NAME
            ),
            None,
        )
        candidates = [forced] if forced is not None else pool
        if sector is not None and forced is None:
            candidates = [s for s in pool if s.sector == sector]
        if not candidates:
            raise ValueError("guess_company 풀이 비어 있음(섹터 필터 결과 0)")
        answer = self._rng.choice(candidates)
        quiz_id = _new_id()
        sector_txt = answer.sector.value if answer.sector else "미분류"
        lines = [
            "다음 힌트로 회사를 맞혀보세요.",
            f"- 섹터: **{sector_txt}**",
            f"- 현재가: {_fmt_price(answer)}",
        ]
        if answer.market_cap_rank is not None:  # 랭크 있을 때만(없으면 줄 생략)
            lines.append(f"- 시총 {answer.market_cap_rank}위권")
        question_md = "\n".join(lines)
        hint_policy = "chosung" if answer.market == Market.KR else "first_letter"
        question = QuizQuestion(
            quiz_id=quiz_id,
            quiz_type=QuizType.COMPANY,
            question_md=question_md,
            hint_policy=hint_policy,
        )
        state = QuizState(
            quiz_id=quiz_id,
            quiz_type=QuizType.COMPANY,
            answer=answer,
            hints_precomputed=self._name_hints(answer),
            created_at=self._clock(),
        )
        return question, state
