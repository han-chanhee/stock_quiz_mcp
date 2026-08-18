"""카카오 Tools 위젯 조립 함수 테스트."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from contracts.schemas import LeaderboardSnapshot, ScoreEntry
from server.widgets import (
    correct_answer_widget,
    leaderboard_table_rows,
    quiz_question_widget,
    to_content_text,
    wrong_answer_widget,
)


def _leaderboard() -> LeaderboardSnapshot:
    now = datetime.now(timezone.utc)
    entries = [
        ScoreEntry(identity_key=f"user-{rank}", display_name=f"사용자{rank}", score=60 - rank, updated_at=now)
        for rank in range(1, 7)
    ]
    return LeaderboardSnapshot(
        top=entries,
        my_entry=entries[-1],
        my_rank=6,
        week_started_at=now,
    )


def _assert_payload(payload: dict, expected_name: str) -> None:
    assert set(payload) == {"widget", "copy_text", "name"}
    assert payload["name"] == expected_name
    assert payload["widget"]["type"] in {"Card", "ListView"}
    restored = json.loads(json.dumps(payload, ensure_ascii=False))
    assert restored == payload

    def has_status(value: object) -> bool:
        if isinstance(value, dict):
            return "status" in value or any(has_status(item) for item in value.values())
        if isinstance(value, list):
            return any(has_status(item) for item in value)
        return False

    assert not has_status(payload)


def test_quiz_question_widget_payload() -> None:
    payload = quiz_question_widget("QZ-한글", "종목 퀴즈\n국내 시장", "**힌트**: 반도체")
    _assert_payload(payload, "quiz_question")
    assert payload["widget"]["children"][3]["type"] == "Col"
    assert "QZ-한글" in payload["copy_text"]


def test_quiz_question_widget_uses_mode_independent_title_and_question() -> None:
    question_md = "**삼성전자**의 현재 주가는 얼마일까요?"
    payload = quiz_question_widget("QZ-1", "📈 주가 퀴즈 — 가격 맞히기", question_md)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "이 기업의 종목명은?" not in serialized
    assert question_md in serialized


def test_wrong_answer_widget_payload() -> None:
    payload = wrong_answer_widget("초성은 ㅅㅅㅈㅈ", 2)
    _assert_payload(payload, "wrong_answer")
    assert payload["widget"]["children"][1]["color"] == "warning"


def test_correct_answer_widget_payload_and_top_five() -> None:
    leaderboard = _leaderboard()
    payload = correct_answer_widget(
        "삼성전자",
        "현재가 80,000원",
        "시가총액 1위",
        "특별한 재료 확인 안 됨",
        10,
        leaderboard,
        ["다음 퀴즈", "종료"],
    )
    _assert_payload(payload, "correct_answer")
    table = next(child for child in payload["widget"]["children"] if child["type"] == "Table")
    assert len(table["children"]) == 6
    assert "나의 순위: 6위" in payload["copy_text"]


def test_correct_answer_widget_without_leaderboard() -> None:
    payload = correct_answer_widget("삼성전자", "가격", "순위", "재료", None, None, ["종료"])
    _assert_payload(payload, "correct_answer")
    assert all(child["type"] != "Table" for child in payload["widget"]["children"])


def test_leaderboard_table_rows_schema() -> None:
    table = leaderboard_table_rows(_leaderboard())
    assert table["type"] == "Table"
    assert table["children"][0]["type"] == "Table.Row"
    assert table["children"][0]["header"] is True
    assert table["children"][0]["children"][0]["type"] == "Table.Cell"


def test_to_content_text_preserves_korean() -> None:
    text = to_content_text({"한글": "그대로"})
    assert text == '{"한글": "그대로"}'
    assert json.loads(text) == {"한글": "그대로"}
