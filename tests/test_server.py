"""모듈 E 테스트: 캐시 로드 + 풀 시나리오 왕복 + NOT_FOUND + 개인 중복 제출.

fastmcp 없이 handlers/cache만으로 오케스트레이션을 검증한다(툴 등록은 main.py 얇은 래핑).
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from clients import MockMarketClient
from batch import DailyBatch, MockReasonProvider
from contracts.schemas import Market, Period, Verdict
from server.cache import DataValidationError, QuizCache
from server.handlers import DISCLAIMER, QuizHandlers
from services.quiz_bank import QuizBank
from store import QuizStore, ScoreStore


@pytest.fixture
async def cache(tmp_path) -> QuizCache:
    # 배치로 tmp에 산출물 생성 후 캐시 로드
    await DailyBatch(
        MockMarketClient(), data_dir=tmp_path, reason_provider=MockReasonProvider()
    ).run()
    return QuizCache(tmp_path).load()


def _handlers(cache: QuizCache, seed: int = 0) -> tuple[QuizHandlers, QuizStore]:
    store = QuizStore()
    score_store = ScoreStore()
    return QuizHandlers(cache, store, score_store, QuizBank(rng=random.Random(seed))), store


@pytest.mark.asyncio
async def test_cache_loads_and_not_stale(cache):
    assert cache.stale is False
    assert cache.data_as_of is not None
    assert cache.top20(Market.KR)
    assert cache.movers(Market.KR, Period.WEEK, "up")
    assert cache.sector_pool()


@pytest.mark.asyncio
async def test_cache_old_data_is_stale(tmp_path):
    """배치 파일이 존재해도 기준 시각이 오래됐으면 stale로 표시한다."""
    await DailyBatch(
        MockMarketClient(), data_dir=tmp_path, reason_provider=MockReasonProvider()
    ).run()
    old_as_of = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

    def replace_as_of(value):
        if isinstance(value, dict):
            return {
                key: old_as_of if key == "as_of" else replace_as_of(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [replace_as_of(item) for item in value]
        return value

    for path in tmp_path.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(
            json.dumps(replace_as_of(data), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    assert QuizCache(tmp_path).load().stale is True


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
    handlers = QuizHandlers(cache, store, ScoreStore(), QuizBank(rng=_random.Random(0)),
                            rng=_random.Random(0))

    # 주가 모드
    p = handlers.quiz(QuizMode.PRICE, "테스터", Market.KR)
    assert "주가 퀴즈" in p.markdown and p.quiz_id
    assert store.get(p.quiz_id).quiz_type.value == "price"

    # 종목 모드
    s = handlers.quiz(QuizMode.STOCK, "테스터", Market.KR)
    assert "종목 퀴즈" in s.markdown and s.quiz_id
    assert store.get(s.quiz_id).quiz_type.value == "company"
    assert store.get(s.quiz_id).answer.name not in s.markdown  # 정답 미노출

    # 시장 모드 (방향 랜덤) — gainer/loser 중 하나
    m = handlers.quiz(QuizMode.MARKET, "테스터", Market.KR, Period.WEEK)
    assert "시장 퀴즈" in m.markdown and m.quiz_id
    assert store.get(m.quiz_id).quiz_type.value in ("gainer", "loser")

    # US는 모드와 무관하게 차단
    from server.handlers import _US_BLOCKED_MD
    assert handlers.quiz(QuizMode.PRICE, "테스터", Market.US).markdown == _US_BLOCKED_MD


@pytest.mark.asyncio
async def test_market_mode_random_covers_both_directions(cache):
    """시장 모드가 상승·하락 둘 다 낼 수 있는지(랜덤) 확인."""
    import random as _random

    from server.handlers import QuizMode

    kinds = set()
    for seed in range(12):
        store = QuizStore()
        h = QuizHandlers(cache, store, ScoreStore(), QuizBank(rng=_random.Random(seed)),
                         rng=_random.Random(seed))
        out = h.quiz(QuizMode.MARKET, "테스터", Market.KR, Period.WEEK)
        kinds.add(store.get(out.quiz_id).quiz_type.value)
    assert kinds == {"gainer", "loser"}  # 두 방향 모두 등장


@pytest.mark.asyncio
async def test_full_scenario_price_quiz(cache):
    handlers, store = _handlers(cache)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)
    assert state is not None
    assert out.widget is not None
    assert out.widget["name"] == "price_quiz"

    # 오답 → 힌트(UP/DOWN)
    wrong = await handlers.submit_answer(out.quiz_id, str(state.answer.price * 0.5), "테스터")
    assert wrong.verdict == Verdict.WRONG
    assert "UP" in wrong.markdown or "DOWN" in wrong.markdown
    assert wrong.widget is not None
    assert wrong.widget["name"] == "wrong_answer"

    # 정답 → 미니분석 + 2택 + 면책 문구
    correct = await handlers.submit_answer(out.quiz_id, str(state.answer.price), "테스터")
    assert correct.verdict == Verdict.CORRECT
    assert correct.analysis is not None
    assert DISCLAIMER in correct.markdown
    assert "미니분석" in correct.markdown          # 미니분석 자동 표시
    assert correct.next_actions == ["다음 퀴즈", "다른 퀴즈", "종료"]
    assert correct.widget is not None
    assert correct.widget["name"] == "correct_answer"


@pytest.mark.asyncio
async def test_first_try_correct_adds_three_points_and_ranking(cache):
    """첫 시도 정답은 3점과 TOP3 및 본인 순위/점수를 함께 반환한다."""
    store = QuizStore()
    score_store = ScoreStore()
    handlers = QuizHandlers(cache, store, score_store)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)

    correct = await handlers.submit_answer(
        out.quiz_id, str(state.answer.price), "첫정답"
    )

    assert "3점" in correct.markdown
    assert "주간 TOP3" in correct.markdown
    assert "내 점수 3점 · 1위" in correct.markdown
    assert correct.leaderboard is not None
    assert correct.leaderboard.my_rank == 1


@pytest.mark.asyncio
async def test_same_user_does_not_add_score_twice(cache):
    """같은 사용자의 같은 퀴즈 재제출은 점수와 랭킹을 다시 계산하지 않는다."""
    store = QuizStore()
    score_store = ScoreStore()
    handlers = QuizHandlers(cache, store, score_store)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)
    answer = str(state.answer.price)

    first = await handlers.submit_answer(out.quiz_id, answer, "중복방지")
    repeated = await handlers.submit_answer(out.quiz_id, answer, "중복방지")

    assert first.leaderboard is not None
    assert first.leaderboard.my_entry.score == 3
    assert repeated.widget is not None
    assert "내 점수 3점" in repeated.widget["copy_text"]
    assert score_store.leaderboard("중복방지").my_entry.score == 3


@pytest.mark.asyncio
async def test_wrong_answer_subtracts_score_and_shows_ranking(cache):
    """오답은 1점 감점하고 TOP3 및 본인 순위/점수를 함께 반환한다."""
    handlers, store = _handlers(cache)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)

    wrong = await handlers.submit_answer(
        out.quiz_id, str(state.answer.price * 0.5), "오답자"
    )

    assert wrong.leaderboard is not None
    assert wrong.leaderboard.my_entry.score == -1
    assert "점수 1점 감점" in wrong.markdown
    assert "주간 TOP3" in wrong.markdown
    assert "내 점수 -1점" in wrong.markdown


@pytest.mark.asyncio
async def test_wrong_answer_widget_shows_live_leaderboard(cache):
    """매턴 랭킹 UX: 오답도 위젯에는 현재 점수와 순위를 포함한다."""
    handlers, store = _handlers(cache)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)

    wrong = await handlers.submit_answer(
        out.quiz_id, str(state.answer.price * 0.5), "오답자"
    )

    assert wrong.leaderboard is not None
    assert wrong.widget is not None
    assert "점수 1점 감점" in wrong.widget["copy_text"]
    assert "내 점수 -1점" in wrong.widget["copy_text"]


@pytest.mark.asyncio
async def test_wrong_then_correct_applies_penalty_then_reward(cache):
    """한 번 틀리고 맞히면 -1점 후 2점 획득으로 순점수 1점이 된다."""
    store = QuizStore()
    score_store = ScoreStore()
    handlers = QuizHandlers(cache, store, score_store)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)

    wrong = await handlers.submit_answer(
        out.quiz_id, str(state.answer.price * 0.5), "도전자"
    )
    correct = await handlers.submit_answer(
        out.quiz_id, str(state.answer.price), "도전자"
    )

    assert wrong.leaderboard.my_entry.score == -1
    assert "점수 1점 감점" in wrong.markdown
    assert "이번 정답으로 **2점** 획득" in correct.markdown
    assert correct.leaderboard.my_entry.score == 1
    assert "내 점수 1점" in correct.markdown


@pytest.mark.asyncio
async def test_quiz_widget_shows_live_leaderboard(cache):
    """출제 턴도 현재 주간 랭킹을 같은 하단 패널로 보여준다."""
    from server.handlers import QuizMode

    store = QuizStore()
    score_store = ScoreStore()
    handlers = QuizHandlers(cache, store, score_store)
    await score_store.add_result("랭커", "랭커", 1)

    out = handlers.quiz(QuizMode.PRICE, "랭커", Market.KR)

    assert out.widget is not None
    assert "주간 TOP3" in out.widget["copy_text"]
    assert "내 점수 3점" in out.widget["copy_text"]


@pytest.mark.asyncio
async def test_oauth_identity_key_scores_under_stable_identity(cache):
    """OAuth/플랫폼 식별자가 있으면 닉네임은 표시명, 점수 키는 식별자로 쓴다."""
    store = QuizStore()
    score_store = ScoreStore()
    handlers = QuizHandlers(cache, store, score_store)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)

    correct = await handlers.submit_answer(
        out.quiz_id,
        str(state.answer.price),
        "화면닉",
        identity_key="oauth-user-1",
    )

    assert correct.leaderboard is not None
    assert correct.leaderboard.my_entry.identity_key == "oauth-user-1"
    assert correct.leaderboard.my_entry.display_name == "화면닉"
    assert score_store.leaderboard("oauth-user-1").my_entry.score == 3
    assert score_store.leaderboard("화면닉").my_entry.score == 0


@pytest.mark.asyncio
async def test_blank_nickname_grades_without_score(cache):
    """공백 닉네임이어도 정답 처리는 하되 점수는 부여하지 않는다."""
    store = QuizStore()
    score_store = ScoreStore()
    handlers = QuizHandlers(cache, store, score_store)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)

    correct = await handlers.submit_answer(
        out.quiz_id, str(state.answer.price), "   "
    )

    assert correct.verdict == Verdict.CORRECT
    assert correct.leaderboard is None
    assert correct.widget is not None
    assert correct.widget["name"] == "correct_answer"
    assert "닉네임이 없어" in correct.markdown
    assert score_store.rank_of("   ") == 1


@pytest.mark.asyncio
async def test_name_quiz_wrong_then_hint_then_correct(cache):
    handlers, store = _handlers(cache, seed=3)
    out = handlers.top_gainers_quiz(Market.KR, Period.WEEK)
    state = store.get(out.quiz_id)
    # 1차 오답 → 초성 힌트
    r1 = await handlers.submit_answer(out.quiz_id, "없는종목", "테스터")
    assert r1.verdict == Verdict.WRONG
    assert state.hints_precomputed[0].text in r1.markdown  # 초성 단계
    # 정답
    r2 = await handlers.submit_answer(out.quiz_id, state.answer.name, "테스터")
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
    r = await handlers.submit_answer("does-not-exist", "삼성전자", "테스터")
    assert r.verdict == Verdict.NOT_FOUND
    assert r.widget is not None
    assert r.widget["name"] == "quiz_not_found"


@pytest.mark.asyncio
async def test_same_user_concurrent_duplicate_is_not_scored_twice(cache):
    """같은 사용자가 같은 quiz_id를 동시에 여러 번 맞혀도 점수는 1회만."""
    store = QuizStore()
    score_store = ScoreStore()
    handlers = QuizHandlers(cache, store, score_store)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)
    answer = str(state.answer.price)

    results = await asyncio.gather(
        *[
            handlers.submit_answer(
                out.quiz_id,
                answer,
                "화면닉",
                identity_key="oauth-same-user",
            )
            for _ in range(5)
        ]
    )

    winners = [r for r in results if r.analysis is not None]
    assert len(winners) == 1
    assert score_store.leaderboard("oauth-same-user").my_entry.score == 3


@pytest.mark.asyncio
async def test_no_advice_on_leading_question(cache):
    """유도 질문에도 매수/매도 권유 문구가 절대 출력되지 않는다."""
    handlers, store = _handlers(cache)
    out = handlers.price_quiz(Market.KR)
    state = store.get(out.quiz_id)
    correct = await handlers.submit_answer(out.quiz_id, str(state.answer.price), "테스터")
    for banned in ("매수", "매도", "사세요", "추천"):
        assert banned not in correct.markdown


@pytest.mark.asyncio
async def test_refresh_today_updates_movers_and_top20(cache):
    """당일 마켓 마감 릭킹과 시총 상위 종목을 함께 갱신한다."""
    from server.main import _refresh_today

    client = MockMarketClient()
    cache._top20 = {}  # 리프레셔가 실제로 다시 채우는지 검증
    await _refresh_today(cache, client)

    for market in (Market.KR, Market.US):
        assert cache.top20(market)
        assert cache.movers(market, Period.TODAY, "up")
        assert cache.movers(market, Period.TODAY, "down")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("now", "expected_refreshes"),
    [
        (datetime(2026, 8, 14, 10, 0, tzinfo=timezone(timedelta(hours=9))), 1),
        (datetime(2026, 8, 14, 16, 0, tzinfo=timezone(timedelta(hours=9))), 0),
    ],
)
async def test_refresher_loop_runs_only_during_market_hours(
    cache, monkeypatch, now, expected_refreshes
):
    """리프레셔는 장중에만 갱신하고 매 tick을 60초 간격으로 두다."""
    import server.main as main

    refreshes = 0
    sleeps = []

    async def fake_refresh(cache, client):
        nonlocal refreshes
        refreshes += 1

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(main, "_now_kst", lambda: now)
    monkeypatch.setattr(main, "_refresh_today", fake_refresh)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await main._refresher_loop(cache, MockMarketClient())

    assert refreshes == expected_refreshes
    assert sleeps == [main._REFRESH_INTERVAL_SEC]


def test_build_app_requires_score_store(cache):
    """점수 저장소를 빠뜨리면 조립 시점에 즉시 실패한다."""
    from server.main import build_app

    with pytest.raises(TypeError):
        build_app(cache, QuizStore())


@pytest.mark.asyncio
async def test_tool_returns_widget_json_and_markdown_fallback(cache):
    """툴 경로는 위젯을 JSON으로, 위젯 없는 안내는 마크다운으로 반환한다."""
    from server.handlers import _US_BLOCKED_MD
    from server.main import build_app

    store = QuizStore()
    app = build_app(cache, store, ScoreStore(), QuizBank(rng=random.Random(0)))

    quiz_tool = await app.get_tool("quiz")
    submit_tool = await app.get_tool("submit_answer")
    quiz_result = quiz_tool.fn(mode="주가", nickname="테스터")
    quiz_payload = json.loads(quiz_result)
    assert quiz_payload["name"] == "price_quiz"

    quiz_id = next(iter(store._data))
    state = store.get(quiz_id)
    wrong_result = await submit_tool.fn(
        quiz_id=quiz_id,
        answer=str(state.answer.price * 0.5),
        nickname="테스터",
    )
    wrong_payload = json.loads(wrong_result)
    assert wrong_payload["name"] == "wrong_answer"

    # US 차단도 이제 위젯으로 반환된다(더 이상 마크다운 폴백 아님).
    us_result = quiz_tool.fn(mode="주가", nickname="테스터", market="US")
    us_payload = json.loads(us_result)
    assert us_payload["name"] == "us_blocked"
    assert _US_BLOCKED_MD in us_payload["copy_text"] or "해외" in us_payload["copy_text"]


@pytest.mark.asyncio
async def test_tool_scoring_ignores_oauth_client_id_without_user_subject(cache):
    """OAuth client_id만 있으면 앱 ID라서 사용자 점수 키로 쓰지 않고 닉네임을 쓴다."""
    from server.main import build_app

    class FakeContext:
        client_id = "stockquiz-playmcp-83185073570028966"
        request_context = None

    store = QuizStore()
    score_store = ScoreStore()
    app = build_app(cache, store, score_store, QuizBank(rng=random.Random(0)))

    quiz_tool = await app.get_tool("quiz")
    submit_tool = await app.get_tool("submit_answer")
    quiz_tool.fn(mode="주가", nickname="개인A", ctx=FakeContext())

    quiz_id = next(iter(store._data))
    state = store.get(quiz_id)
    await submit_tool.fn(
        quiz_id=quiz_id,
        answer=str(state.answer.price),
        nickname="개인A",
        ctx=FakeContext(),
    )

    assert score_store.leaderboard("개인A").my_entry.score == 3
    assert score_store.leaderboard("stockquiz-playmcp-83185073570028966").my_entry.score == 0


def test_mcp_trailing_slash_redirect_keeps_forwarded_https(cache):
    """Preview가 /mcp/로 탐색해도 HTTPS에서 HTTP로 다운그레이드하지 않는다."""
    from server.main import _runtime_middleware, build_app

    app = build_app(cache, QuizStore(), ScoreStore()).http_app(
        transport="streamable-http",
        stateless_http=True,
        json_response=True,
        middleware=_runtime_middleware(),
    )

    with TestClient(
        app,
        base_url="http://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io",
        headers={"x-forwarded-proto": "https"},
    ) as client:
        response = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert response.headers["location"] == (
        "https://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io/mcp"
    )


def test_static_oauth_client_accepts_post_and_basic_secret(cache, monkeypatch, tmp_path):
    """PlayMCP가 /token에서 secret을 body나 Basic header로 보내도 둘 다 받는다."""
    import base64
    import hashlib
    import urllib.parse

    from server.auth import _DEFAULT_PLAYMCP_CLIENT_SECRET
    from server.main import _runtime_middleware, create_server

    monkeypatch.setenv("OAUTH_ENABLED", "1")
    monkeypatch.setenv("OAUTH_SNAPSHOT_PATH", str(tmp_path / "oauth.json"))
    client_id = "stockquiz-playmcp-83185073570028966"
    redirect_uri = (
        "https://playmcp.kakao.com/api/v1/applied-mcps/"
        "83185073570028966/authorize/oauth:callback"
    )
    app = create_server().http_app(
        transport="streamable-http",
        stateless_http=True,
        json_response=True,
        middleware=_runtime_middleware(),
    )

    def issue_code(client: TestClient, label: str) -> tuple[str, str]:
        verifier = f"codex-{label}-verifier-012345678901234567890123456789"
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        response = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": label,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        location = response.headers["location"]
        if location.startswith("/oauth/consent?token="):
            token = urllib.parse.parse_qs(
                urllib.parse.urlsplit(location).query
            )["token"][0]
            response = client.post(
                "/oauth/consent",
                data={"token": token, "decision": "allow"},
                follow_redirects=False,
            )
            location = response.headers["location"]
        code = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)["code"][0]
        return verifier, code

    with TestClient(
        app,
        base_url="https://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io",
    ) as client:
        verifier, code = issue_code(client, "post")
        post_response = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": _DEFAULT_PLAYMCP_CLIENT_SECRET,
                "code_verifier": verifier,
            },
        )

        verifier, code = issue_code(client, "basic")
        basic = base64.b64encode(
            (
                urllib.parse.quote(client_id, safe="")
                + ":"
                + urllib.parse.quote(_DEFAULT_PLAYMCP_CLIENT_SECRET, safe="")
            ).encode()
        ).decode()
        basic_response = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
            },
            headers={"Authorization": "Basic " + basic},
        )

        verifier, code = issue_code(client, "upper-basic")
        upper_basic_response = client.post(
            "/token",
            data={
                "grant_type": "AUTHORIZATION_CODE",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
            },
            headers={"Authorization": "Basic " + basic},
        )

    assert post_response.status_code == 200
    assert basic_response.status_code == 200
    assert upper_basic_response.status_code == 200


@pytest.mark.asyncio
async def test_weekly_reset_loop_checks_every_minute(monkeypatch):
    """주간 리셋 루프는 현재 KST 시각을 1분마다 확인한다."""
    import server.main as main

    score_store = ScoreStore()
    checks = []
    now = datetime(2026, 8, 17, tzinfo=timezone(timedelta(hours=9)))

    async def fake_reset(value):
        checks.append(value)

    async def fake_sleep(seconds):
        assert seconds == main._WEEKLY_RESET_INTERVAL_SEC
        raise asyncio.CancelledError

    monkeypatch.setattr(score_store, "maybe_weekly_reset", fake_reset)
    monkeypatch.setattr(main, "_now_kst", lambda: now)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await main._weekly_reset_loop(score_store)

    assert checks == [now]


@pytest.mark.asyncio
async def test_snapshot_loop_saves_every_five_minutes(monkeypatch):
    """스냅샷 루프는 5분을 기다린 뒤 저장한다."""
    import server.main as main

    score_store = ScoreStore()
    saves = 0

    async def fake_save():
        nonlocal saves
        saves += 1
        raise asyncio.CancelledError

    async def fake_sleep(seconds):
        assert seconds == main._SNAPSHOT_INTERVAL_SEC

    monkeypatch.setattr(score_store, "snapshot_save", fake_save)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await main._snapshot_loop(score_store)

    assert saves == 1
