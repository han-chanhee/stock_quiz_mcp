"""툴 오케스트레이션(FastMCP 비의존). 캐시 + services + store를 조립한다.

FastMCP는 main.py에서 얇게 감싸기만 한다. 이 계층을 분리해 실 서버 없이도 테스트한다.
모든 반환은 정제된 마크다운 문자열(루트 규칙 9). 원본 JSON 미노출.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from contracts.schemas import (
    GradingResult,
    Market,
    MiniAnalysis,
    Period,
    QuizType,
    Sector,
    Verdict,
)
from services import build_analysis, is_correct, pick_hint
from services.quiz_bank import QuizBank
from store import QuizStore

from .cache import QuizCache

DISCLAIMER = "본 내용은 퀴즈/정보 제공이며 투자 권유가 아닙니다."
_EXPIRES_SEC = 1800

# 해외(US)는 실 KIS 엔드포인트 미검증이라 잠가 둔다. 엔드포인트 확정 후 True로 바꾸면
# 출제 4종이 즉시 US를 지원한다(store/채점/힌트/분석은 이미 시장 무관). 관련: clients/kis.py TODO(US).
US_ENABLED = False
_US_BLOCKED_MD = "🌏 해외 종목 퀴즈는 준비 중입니다. 지금은 국내(KR) 퀴즈만 즐길 수 있어요."


class QuizMode(str, Enum):
    """사용자가 고르는 3개 모드. 입력을 이 셋으로 강제한다."""

    PRICE = "주가"      # 주가 퀴즈: 현재가 맞히기
    MARKET = "시장"     # 시장 퀴즈: 많이 오른/떨어진 종목 맞히기(방향 랜덤)
    STOCK = "종목"      # 종목 퀴즈: 섹터·가격·시총 힌트로 회사 맞히기


# 모드 선택 시 문제 앞에 붙는 설명 1줄 (팩트만, 권유 문구 없음)
_MODE_INTRO = {
    QuizMode.PRICE: "📈 **주가 퀴즈** — 종목의 현재 주가를 맞혀보세요. 정답가 ±3% 이내면 정답!",
    QuizMode.MARKET: "📊 **시장 퀴즈** — 기간 내 가장 많이 오르거나 떨어진 종목을 맞혀보세요.",
    QuizMode.STOCK: "🏢 **종목 퀴즈** — 섹터·현재가·시총순위 힌트로 어떤 회사인지 맞혀보세요.",
}


@dataclass
class QuizOutcome:
    quiz_id: str
    markdown: str


@dataclass
class SubmitOutcome:
    verdict: Verdict
    markdown: str
    analysis: MiniAnalysis | None = None
    attempts: int = 0
    next_actions: list[str] = field(default_factory=list)


def _as_of_footer(cache: QuizCache) -> str:
    if cache.data_as_of is None:
        return ""
    stamp = cache.data_as_of.strftime("%Y-%m-%d %H:%M")
    stale = " ⚠️낡은 데이터" if cache.stale else ""
    return f"\n\n_기준시각: {stamp}{stale}_"


class QuizHandlers:
    """5개 툴의 실제 로직."""

    def __init__(
        self,
        cache: QuizCache,
        store: QuizStore,
        bank: QuizBank | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._cache = cache
        self._store = store
        self._bank = bank or QuizBank()
        self._rng = rng or random.Random()  # 시장 퀴즈 방향(상승/하락) 랜덤 선택용

    # ── 통합 진입점: 3개 모드 ────────────────────────────────

    def quiz(
        self,
        mode: QuizMode,
        market: Market = Market.KR,
        period: Period = Period.TODAY,
        sector: Sector | None = None,
    ) -> QuizOutcome:
        """모드 하나를 받아 [모드 설명 + 퀴즈]를 반환한다. 입력은 3모드로 강제된다."""
        if (blocked := self._us_guard(market)) is not None:
            return blocked

        if mode == QuizMode.PRICE:
            outcome = self.price_quiz(market)
        elif mode == QuizMode.STOCK:
            outcome = self.guess_company(sector, market)
        elif mode == QuizMode.MARKET:
            # 방향(상승/하락)은 랜덤
            if self._rng.random() < 0.5:
                outcome = self.top_gainers_quiz(market, period)
            else:
                outcome = self.top_losers_quiz(market, period)
        else:  # 방어적: 알 수 없는 모드
            return QuizOutcome(quiz_id="", markdown="주가 / 시장 / 종목 중에서 골라주세요.")

        # 문제 앞에 모드 설명 삽입 (quiz_id가 없는 안내 응답엔 붙이지 않음)
        if outcome.quiz_id:
            outcome = QuizOutcome(
                outcome.quiz_id, f"{_MODE_INTRO[mode]}\n\n{outcome.markdown}"
            )
        return outcome

    # ── 출제 4종 (내부 구현 — quiz()가 라우팅) ────────────────

    def _us_guard(self, market: Market) -> QuizOutcome | None:
        """US 잠금 가드. 잠긴 경우 안내 마크다운(quiz_id 없음)을 반환한다."""
        if not US_ENABLED and market == Market.US:
            return QuizOutcome(quiz_id="", markdown=_US_BLOCKED_MD)
        return None

    def _register(self, question, state) -> QuizOutcome:
        self._store.put(state)
        md = (
            f"{question.question_md}\n\n"
            f"`quiz_id`: **{question.quiz_id}** (제한시간 30분)\n"
            f"→ `submit_answer(quiz_id, answer)`로 정답을 제출하세요."
        )
        return QuizOutcome(question.quiz_id, md + _as_of_footer(self._cache))

    def price_quiz(self, market: Market = Market.KR) -> QuizOutcome:
        if (blocked := self._us_guard(market)) is not None:
            return blocked
        pool = self._cache.top20(market)
        question, state = self._bank.price_quiz(pool)
        return self._register(question, state)

    def top_gainers_quiz(
        self, market: Market = Market.KR, period: Period = Period.TODAY
    ) -> QuizOutcome:
        if (blocked := self._us_guard(market)) is not None:
            return blocked
        ranking = self._cache.movers(market, period, "up")
        question, state = self._bank.movers_quiz(ranking, QuizType.GAINER, market)
        return self._register(question, state)

    def top_losers_quiz(
        self, market: Market = Market.KR, period: Period = Period.TODAY
    ) -> QuizOutcome:
        if (blocked := self._us_guard(market)) is not None:
            return blocked
        ranking = self._cache.movers(market, period, "down")
        question, state = self._bank.movers_quiz(ranking, QuizType.LOSER, market)
        return self._register(question, state)

    def guess_company(
        self, sector: Sector | None = None, market: Market = Market.KR
    ) -> QuizOutcome:
        if (blocked := self._us_guard(market)) is not None:
            return blocked
        pool = self._cache.sector_pool()
        # 데이터가 얇아 해당 섹터에 종목이 없을 수 있다(크래시 대신 안내).
        if sector is not None and not [s for s in pool if s.sector == sector]:
            return QuizOutcome(
                quiz_id="",
                markdown=f"🗂️ '{sector.value}' 섹터는 아직 준비된 종목이 부족해요. "
                "섹터를 비워두면 전체에서 출제해 드려요.",
            )
        if not pool:
            return QuizOutcome(quiz_id="", markdown="🗂️ 회사 맞히기 데이터를 준비 중이에요.")
        question, state = self._bank.company_quiz(pool, sector)
        return self._register(question, state)

    # ── 채점 ─────────────────────────────────────────────────

    async def submit_answer(self, quiz_id: str, answer: str) -> SubmitOutcome:
        state, miss = self._store.get_state_or_verdict(quiz_id)
        if state is None:
            if miss == Verdict.EXPIRED:
                return SubmitOutcome(Verdict.EXPIRED, "⏰ 만료된 퀴즈입니다. 새 퀴즈를 출제해주세요.")
            return SubmitOutcome(Verdict.NOT_FOUND, "❓ 존재하지 않는 quiz_id 입니다.")

        if state.solved:
            return SubmitOutcome(
                Verdict.CORRECT, "🏁 이미 정답이 나온 퀴즈입니다.", attempts=state.attempts
            )

        aliases = self._cache.aliases()
        if is_correct(state, answer, aliases):
            solved_state, was_first = await self._store.compare_and_solve(quiz_id)
            if not was_first:
                return SubmitOutcome(
                    Verdict.CORRECT,
                    "🏁 이미 정답이 나온 퀴즈입니다.",
                    attempts=(solved_state.attempts if solved_state else 0),
                )
            reason = self._cache.reason(state.answer.ticker)
            analysis = build_analysis(state.answer, reason)
            result = GradingResult(
                verdict=Verdict.CORRECT,
                analysis=analysis,
                attempts=solved_state.attempts if solved_state else 0,
                # 정답 후 흐름: 미니분석은 위에서 자동 표시 + 아래 3택 분기
                next_actions=["다음 퀴즈", "다른 퀴즈", "종료"],
            )
            md = self._render_correct(state.answer.name, result)
            return SubmitOutcome(
                Verdict.CORRECT,
                md,
                analysis=analysis,
                attempts=result.attempts,
                next_actions=result.next_actions,
            )

        # 오답
        updated = await self._store.record_attempt(quiz_id)
        attempts = updated.attempts if updated else 1
        hint = pick_hint(state, answer, attempts)
        md = (
            f"❌ 오답입니다. (시도 {attempts}회)\n\n"
            f"💡 힌트: **{hint.text}**"
            + _as_of_footer(self._cache)
        )
        return SubmitOutcome(Verdict.WRONG, md, attempts=attempts)

    def _render_correct(self, name: str, result: GradingResult) -> str:
        a = result.analysis
        lines = [
            f"✅ 정답! **{name}**",
            "",
            "**미니분석**",
            f"- {a.price_line}",
            f"- {a.rank_line}",
            f"- {a.reason_line}",
            "",
            "다음 중 선택: " + " / ".join(f"`{x}`" for x in result.next_actions),
            "",
            f"_{DISCLAIMER}_",  # 미니분석 포함 응답에만 면책 삽입
        ]
        return "\n".join(lines) + _as_of_footer(self._cache)
