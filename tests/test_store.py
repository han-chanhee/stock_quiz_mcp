"""모듈 C 테스트: TTL, 만료, solved 갱신, 상한 축출, 동시 제출 경합."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from contracts.schemas import Market, QuizState, QuizType, StockSnapshot, Verdict
from store import QuizStore, new_quiz_id

_KST = timezone(timedelta(hours=9))


def _snap() -> StockSnapshot:
    return StockSnapshot(
        ticker="005930",
        name="삼성전자",
        market=Market.KR,
        price=78500,
        change_pct=1.2,
        market_cap_rank=1,
        as_of=datetime.now(_KST),
    )


def _state(quiz_id: str, created_at: datetime | None = None) -> QuizState:
    return QuizState(
        quiz_id=quiz_id,
        quiz_type=QuizType.PRICE,
        answer=_snap(),
        created_at=created_at or datetime.now(_KST),
    )


def test_put_get_roundtrip():
    store = QuizStore()
    qid = new_quiz_id()
    store.put(_state(qid))
    got = store.get(qid)
    assert got is not None and got.quiz_id == qid


def test_new_quiz_id_unguessable_and_unique():
    ids = {new_quiz_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(len(i) >= 8 for i in ids)


def test_ttl_expiry_returns_none_and_deletes():
    store = QuizStore(ttl_sec=1800)
    qid = new_quiz_id()
    old = datetime.now(_KST) - timedelta(minutes=31)
    store.put(_state(qid, created_at=old))
    assert store.get(qid) is None          # 만료
    assert len(store) == 0                  # 내부 삭제됨


def test_get_state_or_verdict_distinguishes():
    store = QuizStore(ttl_sec=1800)
    qid = new_quiz_id()
    store.put(_state(qid))
    state, miss = store.get_state_or_verdict(qid)
    assert state is not None and miss is None
    # 부재
    _, miss = store.get_state_or_verdict("nope")
    assert miss == Verdict.NOT_FOUND
    # 만료
    exp = new_quiz_id()
    store.put(_state(exp, created_at=datetime.now(_KST) - timedelta(hours=1)))
    _, miss = store.get_state_or_verdict(exp)
    assert miss == Verdict.EXPIRED


def test_solved_update():
    store = QuizStore()
    qid = new_quiz_id()
    st = _state(qid)
    store.put(st)
    st.solved = True
    st.attempts = 3
    store.update(st)
    got = store.get(qid)
    assert got.solved is True and got.attempts == 3


def test_eviction_over_capacity():
    store = QuizStore(max_entries=10)
    ids = [new_quiz_id() for _ in range(15)]
    for qid in ids:
        store.put(_state(qid))
    assert len(store) == 10
    # 가장 오래된 5개는 축출
    assert store.get(ids[0]) is None
    assert store.get(ids[-1]) is not None


def test_purge_expired_counts():
    store = QuizStore(ttl_sec=1800)
    fresh = new_quiz_id()
    store.put(_state(fresh))
    for _ in range(3):
        store.put(_state(new_quiz_id(),
                         created_at=datetime.now(_KST) - timedelta(hours=1)))
    assert store.purge_expired() == 3
    assert store.get(fresh) is not None


@pytest.mark.asyncio
async def test_concurrent_solve_only_one_winner():
    store = QuizStore()
    qid = new_quiz_id()
    store.put(_state(qid))

    async def attempt():
        _, was_first = await store.compare_and_solve(qid)
        return was_first

    results = await asyncio.gather(*[attempt() for _ in range(5)])
    assert sum(results) == 1  # 중복 제출이어도 점수는 1회만 반영


@pytest.mark.asyncio
async def test_concurrent_record_attempt_no_lost_update():
    store = QuizStore()
    qid = new_quiz_id()
    store.put(_state(qid))
    await asyncio.gather(*[store.record_attempt(qid) for _ in range(20)])
    assert store.get(qid).attempts == 20
