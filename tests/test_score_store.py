"""주간 점수 저장소의 점수, 순위, 리셋, 스냅샷을 검증한다."""

from datetime import datetime, timedelta, timezone

import pytest

from contracts.schemas import ScoreEntry
from store.score_store import ScoreStore

_KST = timezone(timedelta(hours=9))


@pytest.mark.asyncio
async def test_attempts_have_differential_scores(tmp_path):
    store = ScoreStore(snapshot_path=tmp_path / "scores.json")

    assert await store.add_result("first", "첫번째", 1) == 3
    assert await store.add_result("second", "두번째", 2) == 2
    assert await store.add_result("third", "세번째", 3) == 1
    assert await store.add_result("later", "네번째", 8) == 1

    assert store.leaderboard("first").my_entry.score == 3
    assert store.leaderboard("second").my_entry.score == 2


@pytest.mark.asyncio
async def test_wrong_answer_penalty_decrements_score(tmp_path):
    store = ScoreStore(snapshot_path=tmp_path / "scores.json")

    assert await store.add_penalty("wrong", "오답자") == -1
    assert store.leaderboard("wrong").my_entry.score == -1
    assert await store.add_result("wrong", "오답자", 1) == 3
    assert store.leaderboard("wrong").my_entry.score == 2


@pytest.mark.asyncio
async def test_score_store_stats_track_play_counts(tmp_path):
    store = ScoreStore(snapshot_path=tmp_path / "scores.json")
    store.record_quiz_started("u1")
    store.record_quiz_started("u1")
    store.record_quiz_started("u2")
    await store.add_penalty("u1", "주식러1")
    await store.add_result("u1", "주식러1", 1)
    await store.snapshot_save()

    restored = ScoreStore(snapshot_path=tmp_path / "scores.json")
    restored.snapshot_load()
    stats = restored.stats()

    assert stats["participants"] == 1
    assert stats["quiz_players"] == 2
    assert stats["quiz_starts"] == 3
    assert stats["submitted_answers"] == 2
    assert stats["correct_answers"] == 1
    assert stats["wrong_answers"] == 1
    assert stats["top"][0]["display_name"] == "주식러1"


@pytest.mark.asyncio
async def test_leaderboard_top_three_and_exact_rank(monkeypatch, tmp_path):
    store = ScoreStore(snapshot_path=tmp_path / "scores.json")
    reached = iter(
        datetime(2026, 8, 10, 9, minute, tzinfo=_KST) for minute in range(6)
    )
    monkeypatch.setattr(store, "_now", lambda: next(reached))

    for index in range(6):
        await store.add_result(f"user-{index}", f"사용자 {index}", 1)

    board = store.leaderboard("user-5")
    assert [entry.identity_key for entry in board.top] == [
        "user-0", "user-1", "user-2"
    ]
    assert board.my_rank == 6
    assert store.rank_of("unknown") == 7


@pytest.mark.asyncio
async def test_weekly_reset_runs_only_once_per_cycle(tmp_path):
    store = ScoreStore(snapshot_path=tmp_path / "scores.json")
    await store.add_result("alpha", "알파", 1)
    await store.add_result("beta", "베타", 2)
    monday = datetime(2026, 8, 17, 0, 0, tzinfo=_KST)

    assert await store.maybe_weekly_reset(monday) is True
    assert [entry.score for entry in store.leaderboard("alpha").top] == [0, 0]
    assert await store.maybe_weekly_reset(monday + timedelta(days=1)) is False


@pytest.mark.asyncio
async def test_snapshot_round_trip(tmp_path):
    path = tmp_path / "nested" / "scores.json"
    original = ScoreStore(snapshot_path=path)
    await original.add_result("alpha", "알파", 1)
    await original.add_result("beta", "베타", 2)
    await original.snapshot_save()

    restored = ScoreStore(snapshot_path=path)
    restored.snapshot_load()

    assert restored.leaderboard("alpha").model_dump() == original.leaderboard("alpha").model_dump()


@pytest.mark.asyncio
async def test_snapshot_restores_week_metadata(tmp_path):
    path = tmp_path / "scores.json"
    store = ScoreStore(snapshot_path=path)
    monday = datetime(2026, 8, 17, 0, 0, tzinfo=_KST)
    await store.maybe_weekly_reset(monday)
    await store.snapshot_save()

    restored = ScoreStore(snapshot_path=path)
    restored.snapshot_load()

    assert restored.leaderboard("unknown").week_started_at == monday


def test_snapshot_loads_legacy_list_payload(tmp_path):
    path = tmp_path / "legacy.json"
    entry = ScoreEntry(
        identity_key="legacy",
        display_name="레거시",
        score=7,
        updated_at=datetime(2026, 8, 17, tzinfo=_KST),
    )
    path.write_text(f"[{entry.model_dump_json()}]", encoding="utf-8")

    restored = ScoreStore(snapshot_path=path)
    restored.snapshot_load()

    assert restored.leaderboard("legacy").my_entry.score == 7


def test_snapshot_load_sanitizes_legacy_display_name(tmp_path):
    path = tmp_path / "legacy.json"
    entry = ScoreEntry(
        identity_key="legacy",
        display_name="<script>x</script>\n" + "가" * 40,
        score=7,
        updated_at=datetime(2026, 8, 17, tzinfo=_KST),
    )
    path.write_text(f"[{entry.model_dump_json()}]", encoding="utf-8")

    restored = ScoreStore(snapshot_path=path)
    restored.snapshot_load()

    display_name = restored.leaderboard("legacy").my_entry.display_name
    assert "<" not in display_name
    assert "\n" not in display_name
    assert len(display_name) <= 24


@pytest.mark.asyncio
async def test_display_name_is_sanitized_for_ranking_copy(tmp_path):
    store = ScoreStore(snapshot_path=tmp_path / "scores.json")

    await store.add_result(
        "id-1",
        "<script>alert(1)</script>\n**" + "아" * 40,
        1,
    )

    entry = store.leaderboard("id-1").my_entry
    assert "\n" not in entry.display_name
    assert "<" not in entry.display_name
    assert "*" not in entry.display_name
    assert len(entry.display_name) <= 24


def test_corrupt_snapshot_is_quarantined_instead_of_crashing(tmp_path):
    path = tmp_path / "scores.json"
    path.write_text("{broken", encoding="utf-8")

    store = ScoreStore(snapshot_path=path)
    store.snapshot_load()

    assert not path.exists()
    assert list(tmp_path.glob("scores.json.corrupt.*"))
    assert store.leaderboard("unknown").my_entry.score == 0
