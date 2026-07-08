"""모듈 E 테스트: 캐시 로드 + 풀 시나리오 왕복 + NOT_FOUND + 단체방 동시 제출.

fastmcp 없이 handlers/cache만으로 오케스트레이션을 검증한다(툴 등록은 main.py 얇은 래핑).
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path

import pytest

from clients import MockMarketClient
from batch import DailyBatch, MockReasonProvider
from contracts.schemas import Market, Period, Verdict
from server.cache import DataValidationError, QuizCache
from server.handlers import DISCLAIMER, QuizHandlers
from services.quiz_bank import QuizBank
from store import QuizStore


@pytest.fixture
async def cache(tmp_path) -> QuizCache:
    # 배치로 tmp에 산출물 생성 후 캐시 로드
    await DailyBatch(
        MockMarketClient(), data_dir=tmp_path, reason_provider=MockReasonProvider()
    ).run()
    return QuizCache(tmp_path).load()


def _handlers(cache: QuizCache, seed: int = 0) -> tuple[QuizHandlers, QuizStore]:
    store = QuizStore()
    return QuizHandlers(cache, store, QuizBank(rng=random.Random(seed))), store


@pytest.mark.asyncio
async def test_cache_loads_and_not_stale(cache):
    assert cache.stale is False
    assert cache.data_as_of is not None
    assert cache.top20(Market.KR)
    assert cache.movers(Market.KR, Period.WEEK, "up")
    assert cache.sector_pool()


@pytest.mark.asyncio
async def test_cache_missing_dir_is_stale(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    c = QuizCache(empty).load()
    assert c.stale is True  # 파일 부재 → 낡은 상태로 기동


@pytest.mark.asyncio
async def test_cache_us_absent_is_not_stale(tmp_path):
    """US 비활성 — US 파일이 없어도 stale로 오탐하지 않는다(배포 정합성)."""
    await DailyBatch(
        MockMarketClient(), data_dir=tmp_path, reason_provider=MockReasonProvider()
    ).run()
    for f in list(tmp_path.glob("top20_us.json")) + list(tmp_path.glob("movers_US_*.json")):
        f.unlink()
    c = QuizCache(tmp_path).load()
    assert c.stale is False
    assert c.top20(Market.KR)  # KR은 정상


@pytest.mark.asyncio
async def test_guess_company_empty_sector_is_graceful(cache):
    """얇은 데이터로 빈 섹터를 요청해도 크래시 대신 안내 메시지."""
    from contracts.schemas import Sector

    handlers, _ = _handlers(cache)
    cache._sector_pool = [
        s for s in cache.sector_pool() if s.sector == Sector.FINANCE
    ]  # 금융만 남김(화이트박스)
    out = handlers.guess_company(Sector.BIO, Market.KR)
    assert out.quiz_id == "" and "준비" in out.markdown
    assert handlers.guess_company(Sector.FINANCE, Market.KR).quiz_id != ""


@pytest.mark.asyncio
async def test_cache_corrupt_file_aborts(tmp_path):
    (tmp_path / "top20_kr.json").write_text('[{"broken": true}]', encoding="utf-8")
    with pytest.raises(DataValidationError):
        QuizCache(tmp_path).load()


@pytest.mark.asyncio
async def test_quiz_modes_route_and_show_intro(cache):
    """통합 진입점 quiz(mode): 3모드로 라우팅 + 모드 설명 삽입 + 정답 미노출."""
    import random as _random

    from server.handlers import QuizMode

    store = QuizStore()
    handlers = QuizHandlers(cache, store, QuizBank(rng=_random.Random(0)),
                            rng=_random.Random(0))

    # 주가 모드
    p = handlers.quiz(QuizMode.PRICE, Market.KR)
    assert "주가 퀴즈" in p.markdown and p.quiz_id
    assert store.get(p.quiz_id).quiz_type.value == "price"

    # 종목 모드
    s = handlers.quiz(QuizMode.STOCK, Market.KR)
    assert "종목 퀴즈" in s.markdown and s.quiz_id
    assert store.get(s.quiz_id).quiz_type.value == "company"
    assert store.get(s.quiz_id).answer.name not in s.markdown  # 정답 미노출

    # 시장 모드 (방향 랜덤) — gainer/loser 중 하나
    m = handlers.quiz(QuizMode.MARKET, Market.KR, Period.WEEK)
    assert "시장 퀴즈" in m.markdown and m.quiz_id
    assert store.get(m.quiz_id).quiz_type.value in ("gainer", "loser")

    # US는 모드와 무관하게 차단
    from server.handlers import _US_BLOCKED_MD
    assert handlers.quiz(QuizMode.PRICE, Market.US).markdown == _US_BLOCKED_MD


@pytest.mark.asyncio
async def test_market_mode_random_covers_both_directions(cache):
    """시장 모드가 상승·하락 둘 다 낼 수 있는지(랜덤) 확인."""
    import random as _random

    from server.handlers import QuizMode

    kinds = set()
    for seed in range(12):
        store = QuizStore()
        h = QuizHandlers(cache, store, QuizBank(rng=_random.Random(seed)),
                         rng=_random.Random(seed))
        out = h.quiz(QuizMode.MARKET, Market.KR, Period.WEEK)
        kinds.add(store.get(out.quiz_id).quiz_type.value)
    assert kinds == {"gainer", "loser"}  # 두 방향 모두 등장


@pytest.mark.asyncio
async def test_full_scenario_price_quiz(cache):
    handlers, store = _handlers(cache)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)
    assert state is not None

    # 오답 → 힌트(UP/DOWN)
    wrong = await handlers.submit_answer(out.quiz_id, str(state.answer.price * 0.5))
    assert wrong.verdict == Verdict.WRONG
    assert "UP" in wrong.markdown or "DOWN" in wrong.markdown

    # 정답 → 미니분석 + 2택 + 면책 문구
    correct = await handlers.submit_answer(out.quiz_id, str(state.answer.price))
    assert correct.verdict == Verdict.CORRECT
    assert correct.analysis is not None
    assert DISCLAIMER in correct.markdown
    assert "미니분석" in correct.markdown          # 미니분석 자동 표시
    assert correct.next_actions == ["다음 퀴즈", "다른 퀴즈", "종료"]


@pytest.mark.asyncio
async def test_name_quiz_wrong_then_hint_then_correct(cache):
    handlers, store = _handlers(cache, seed=3)
    out = handlers.top_gainers_quiz(Market.KR, Period.WEEK)
    state = store.get(out.quiz_id)
    # 1차 오답 → 초성 힌트
    r1 = await handlers.submit_answer(out.quiz_id, "없는종목")
    assert r1.verdict == Verdict.WRONG
    assert state.hints_precomputed[0].text in r1.markdown  # 초성 단계
    # 정답
    r2 = await handlers.submit_answer(out.quiz_id, state.answer.name)
    assert r2.verdict == Verdict.CORRECT


@pytest.mark.asyncio
async def test_us_market_is_blocked_cleanly(cache):
    """해외(US)는 잠금 상태 — 애매한 에러 대신 안내 메시지, 출제/저장 없음."""
    from server.handlers import _US_BLOCKED_MD

    handlers, store = _handlers(cache)
    for outcome in (
        handlers.price_quiz(Market.US),
        handlers.top_gainers_quiz(Market.US, Period.TODAY),
        handlers.top_losers_quiz(Market.US, Period.TODAY),
        handlers.guess_company(None, Market.US),
    ):
        assert outcome.markdown == _US_BLOCKED_MD
        assert outcome.quiz_id == ""   # store에 아무것도 등록되지 않음
    assert len(store) == 0
    # KR은 정상
    assert handlers.price_quiz(Market.KR).quiz_id != ""


@pytest.mark.asyncio
async def test_not_found_quiz_id(cache):
    handlers, _ = _handlers(cache)
    r = await handlers.submit_answer("does-not-exist", "삼성전자")
    assert r.verdict == Verdict.NOT_FOUND


@pytest.mark.asyncio
async def test_group_chat_only_one_winner(cache):
    """단체방: 같은 quiz_id에 5명 동시 정답 제출 → 1명만 미니분석(선착순)."""
    handlers, store = _handlers(cache)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)
    answer = str(state.answer.price)

    results = await asyncio.gather(
        *[handlers.submit_answer(out.quiz_id, answer) for _ in range(5)]
    )
    winners = [r for r in results if r.analysis is not None]
    assert len(winners) == 1
    assert all(r.verdict == Verdict.CORRECT for r in results)


@pytest.mark.asyncio
async def test_no_advice_on_leading_question(cache):
    """유도 질문에도 매수/매도 권유 문구가 절대 출력되지 않는다."""
    handlers, store = _handlers(cache)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)
    correct = await handlers.submit_answer(out.quiz_id, str(state.answer.price))
    for banned in ("매수", "매도", "사세요", "추천"):
        assert banned not in correct.markdown
