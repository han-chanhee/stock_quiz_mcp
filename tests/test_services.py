"""모듈 B 테스트: 출제 4종/초성/±3% property/별칭 정규화/reason 없음/Reason 검증."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import ValidationError

from contracts.schemas import (
    Market,
    Period,
    QuizQuestion,
    QuizType,
    Reason,
    StockSnapshot,
)
from services import (
    NO_REASON,
    QuizBank,
    build_analysis,
    build_answer_analysis_lines,
    build_question_analysis,
    chart_shape_for_snapshot,
    chosung,
    first_letter_hint,
    is_correct,
    judge_price,
    normalize_name,
    resolve_alias,
)
from services.quiz_bank import chart_points_for_snapshot

_KST = timezone(timedelta(hours=9))


def _snap(name="삼성전자", price=78500.0, market=Market.KR, sector=None, rank=1):
    return StockSnapshot(
        ticker="005930", name=name, market=market, sector=sector,
        price=price, change_pct=1.2, market_cap_rank=rank,
        as_of=datetime.now(_KST),
    )


# ── 출제 4종: 스키마 유효 + 정답 미포함 ──────────────────────

def _pool():
    return [
        _snap("삼성전자", 78500, rank=1),
        _snap("SK하이닉스", 198000, rank=2),
        _snap("LG에너지솔루션", 372000, rank=3),
    ]


def test_price_quiz_valid_and_no_answer_leak():
    bank = QuizBank(rng=random.Random(0))
    q, state = bank.price_quiz(_pool())
    assert isinstance(q, QuizQuestion)
    QuizQuestion.model_validate(q.model_dump())  # 스키마 유효
    # price 퀴즈는 이름을 보여주고 가격을 묻는다 → 가격(정답값)은 노출 안 됨
    assert str(int(state.answer.price)) not in q.question_md
    assert q.hint_policy == "updown"


def test_movers_quiz_hides_name():
    from contracts.schemas import RankingItem
    ranking = [
        RankingItem(rank=i, snapshot=s, period=Period.WEEK)
        for i, s in enumerate(_pool(), start=1)
    ]
    bank = QuizBank(rng=random.Random(0))
    for qtype in (QuizType.GAINER, QuizType.LOSER):
        q, state = bank.movers_quiz(ranking, qtype, Market.KR)
        assert state.answer.name not in q.question_md  # 정답 미노출
        assert len(state.hints_precomputed) == 2       # 초성/첫글자 precompute


def test_chart_quiz_renders_shape_without_answer_name():
    bank = QuizBank(rng=random.Random(0))
    q, state = bank.chart_quiz(_pool())

    assert state.quiz_type == QuizType.COMPANY
    assert state.answer.name not in q.question_md
    assert chart_shape_for_snapshot(state.answer) in q.question_md
    assert "최근 1주 시간봉" in q.question_md
    assert len(state.hints_precomputed) == 2


def test_chart_points_use_one_week_hourly_shape():
    snap = _pool()[0]
    points = chart_points_for_snapshot(snap)
    shape = chart_shape_for_snapshot(snap)

    assert len(points) == 35
    assert all(0.05 <= point <= 0.95 for point in points)
    assert len(shape) == 14


def test_precomputed_hints_never_leak_full_name():
    """회귀(루트규칙14): 라틴/혼합 종목명도 힌트가 정답을 통째로 노출하면 안 됨.

    버그: KR 시장 라틴 종목명(JYP Ent., NAVER)에 초성 힌트 적용 시
    chosung()이 비한글을 그대로 통과시켜 정답명이 유출됐다.
    """
    from contracts.schemas import RankingItem

    leaky = [
        _snap("NAVER", 178500, market=Market.KR, rank=1),
        _snap("JYP Ent.", 62300, market=Market.KR, rank=2),
        _snap("SK하이닉스", 198000, market=Market.KR, rank=3),
    ]
    bank = QuizBank(rng=random.Random(0))
    for snap in leaky:
        ranking = [RankingItem(rank=1, snapshot=snap, period=Period.WEEK)]
        _, state = bank.movers_quiz(ranking, QuizType.GAINER, Market.KR)
        for hint in state.hints_precomputed:
            # 1단계(초성) 힌트는 정답 전체를 담아선 안 된다
            assert snap.name not in hint.text, (
                f"{snap.name} 힌트 유출: {hint.kind}={hint.text!r}"
            )


def test_company_quiz_hides_name_shows_hints():
    bank = QuizBank(rng=random.Random(0))
    q, state = bank.company_quiz(_pool())
    assert state.answer.name not in q.question_md
    assert "현재가" in q.question_md


# ── 초성 변환 ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name,expected",
    [
        ("삼성전자", "ㅅㅅㅈㅈ"),
        ("SK하이닉스", "SKㅎㅇㄴㅅ"),
        ("현대차", "ㅎㄷㅊ"),
        ("포스코", "ㅍㅅㅋ"),
    ],
)
def test_chosung(name, expected):
    assert chosung(name) == expected


def test_first_letter_hint():
    assert first_letter_hint("삼성전자") == "삼___ (4글자)"
    assert first_letter_hint("Tesla") == "T____ (5글자)"


# ── ±3% 판정 (hypothesis property) ───────────────────────────

@given(
    price=st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False),
    ratio=st.floats(min_value=-0.10, max_value=0.10, allow_nan=False),
)
def test_price_tolerance_property(price, ratio):
    # 경계(정확히 3%)의 부동소수 모호구간은 제외하고 성질만 검증
    assume(abs(abs(ratio) - 0.03) > 1e-4)
    answer = _snap(price=price)
    submitted = price * (1 + ratio)
    expected = abs(ratio) <= 0.03
    assert judge_price(answer, str(submitted)) is expected


def test_price_tolerance_exact_boundary_is_correct():
    answer = _snap(price=100000.0)
    assert judge_price(answer, "103000") is True   # 정확히 +3.0%
    assert judge_price(answer, "97000") is True     # 정확히 -3.0%
    assert judge_price(answer, "103100") is False    # +3.1%
    assert judge_price(answer, "abc") is None        # 파싱 실패


# ── 별칭/정규화 (hypothesis 불변성) ──────────────────────────

@given(pad=st.text(alphabet=" \t", max_size=5))
def test_normalize_whitespace_invariant(pad):
    base = "삼성전자"
    assert normalize_name(pad + base + pad) == normalize_name(base)


@given(s=st.text(min_size=1, max_size=20))
def test_normalize_idempotent(s):
    once = normalize_name(s)
    assert normalize_name(once) == once


def test_alias_resolution():
    aliases = {"삼전": "삼성전자", "naver": "NAVER"}
    assert resolve_alias("삼전", aliases) == normalize_name("삼성전자")
    assert resolve_alias(" NAVER ", aliases) == normalize_name("NAVER")
    # 별칭에 없으면 정규화값 그대로
    assert resolve_alias("현대차", aliases) == normalize_name("현대차")


def test_is_correct_with_alias():
    bank = QuizBank(rng=random.Random(0))
    from contracts.schemas import RankingItem
    ranking = [RankingItem(rank=1, snapshot=_snap("삼성전자"), period=Period.TODAY)]
    _, state = bank.movers_quiz(ranking, QuizType.GAINER, Market.KR)
    assert is_correct(state, "삼전", {"삼전": "삼성전자"}) is True
    assert is_correct(state, " 삼성전자 ", {}) is True  # 공백 정규화
    assert is_correct(state, "카카오", {}) is False


# ── reason 없음 / Reason 검증 ────────────────────────────────

def test_analysis_no_reason_returns_fixed_string():
    ana = build_analysis(_snap(rank=1))
    assert ana.reason_line == NO_REASON
    # 매수/매도 권유 문장이 들어가지 않음
    for line in (ana.price_line, ana.rank_line, ana.reason_line):
        assert "매수" not in line and "매도" not in line and "추천" not in line


def test_analysis_uses_reason_when_present():
    r = Reason(
        ticker="005930", text="HBM 수요 강세 보도",
        source_url="https://x.example/n", published_at=datetime.now(_KST),
    )
    ana = build_analysis(_snap(), reason=r)
    assert ana.reason_line == "HBM 수요 강세 보도"


def test_question_analysis_is_five_lines_and_hides_answer_for_name_quizzes():
    snap = _snap("삼성전자", rank=1)
    reason = Reason(
        ticker="005930",
        text="삼성전자 HBM 수요 강세 보도",
        source_url="https://x.example/n",
        published_at=datetime.now(_KST),
    )
    hidden = build_question_analysis(snap, "chart", reason)
    public = build_question_analysis(snap, "price", reason)

    assert len(hidden) == 5
    assert len(public) == 5
    assert all(snap.name not in line for line in hidden)
    assert any(snap.name in line for line in public)
    assert hidden[-1] == "검색 기반 특징: 해당 종목 HBM 수요 강세 보도"
    assert public[-1] == "검색 기반 특징: 삼성전자 HBM 수요 강세 보도"


def test_answer_analysis_is_five_lines_and_differs_from_question_analysis():
    snap = _snap("삼성전자", rank=1)
    reason = Reason(
        ticker="005930",
        text="HBM 수요 강세 보도",
        source_url="https://x.example/n",
        published_at=datetime.now(_KST),
    )
    question_lines = build_question_analysis(snap, "company")
    answer_lines = build_answer_analysis_lines(snap, reason)

    assert len(answer_lines) == 5
    assert answer_lines != question_lines
    assert any(snap.name in line for line in answer_lines)
    assert answer_lines[-1] == "확인된 재료: HBM 수요 강세 보도"


def test_reason_without_source_url_raises():
    with pytest.raises(ValidationError):
        Reason(ticker="005930", text="근거", published_at=datetime.now(_KST))  # source_url 누락
