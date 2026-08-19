"""카카오 Tools 위젯 조립 함수 테스트."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from contracts.schemas import LeaderboardSnapshot, ScoreEntry
from server.widgets import (
    already_solved_widget,
    company_pool_empty_widget,
    company_quiz_widget,
    correct_answer_widget,
    expired_quiz_widget,
    leaderboard_listview_rows,
    leaderboard_table_rows,
    market_quiz_widget,
    mode_selection_widget,
    mode_unknown_widget,
    price_quiz_widget,
    quiz_not_found_widget,
    sector_empty_widget,
    to_content_text,
    us_blocked_widget,
    welcome_widget,
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


def test_price_quiz_widget_payload() -> None:
    payload = price_quiz_widget("QZ-한글", "📈 주가 퀴즈 — 가격 맞히기", "**힌트**: 반도체")
    _assert_payload(payload, "price_quiz")
    assert payload["widget"]["children"][3]["type"] == "Col"
    assert "QZ-한글" in payload["copy_text"]


def test_price_quiz_widget_uses_mode_independent_title_and_question() -> None:
    question_md = "**삼성전자**의 현재 주가는 얼마일까요?"
    payload = price_quiz_widget("QZ-1", "📈 주가 퀴즈 — 가격 맞히기", question_md)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "이 기업의 종목명은?" not in serialized
    assert question_md in serialized


def test_market_quiz_widget_direction_badge_color() -> None:
    up = market_quiz_widget("QZ-2", "📊 시장 퀴즈", "가장 오른 종목은?", 5.2)
    down = market_quiz_widget("QZ-3", "📊 시장 퀴즈", "가장 떨어진 종목은?", -3.1)
    _assert_payload(up, "market_quiz")
    _assert_payload(down, "market_quiz")
    up_badge = next(c for c in up["widget"]["children"] if c.get("type") == "Badge")
    down_badge = next(c for c in down["widget"]["children"] if c.get("type") == "Badge")
    assert up_badge["color"] == "success"
    assert down_badge["color"] == "danger"


def test_company_quiz_widget_payload() -> None:
    question_md = "- 섹터: **반도체**\n- 현재가: 80,000원\n- 시총 1위권"
    payload = company_quiz_widget("QZ-4", "🏢 종목 퀴즈", question_md)
    _assert_payload(payload, "company_quiz")
    assert "QZ-4" in payload["copy_text"]


def test_welcome_widget_payload() -> None:
    payload = welcome_widget()
    _assert_payload(payload, "welcome")
    assert "닉네임" in payload["copy_text"]


def test_mode_selection_widget_payload() -> None:
    payload = mode_selection_widget()
    _assert_payload(payload, "mode_selection")
    assert "닉네임" in payload["copy_text"]


def test_notice_widgets_payload() -> None:
    for factory, expected_name in (
        (already_solved_widget, "already_solved"),
        (expired_quiz_widget, "expired_quiz"),
        (quiz_not_found_widget, "quiz_not_found"),
        (us_blocked_widget, "us_blocked"),
        (company_pool_empty_widget, "company_pool_empty"),
        (mode_unknown_widget, "mode_unknown"),
    ):
        payload = factory()
        _assert_payload(payload, expected_name)


def test_sector_empty_widget_payload() -> None:
    payload = sector_empty_widget("반도체")
    _assert_payload(payload, "sector_empty")
    assert "반도체" in payload["copy_text"]


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
    # Table은 Preview 실측(2026-08-19)에서 정답 위젯 전체를 텍스트로 강등시켜
    # leaderboard_listview_rows(Col+Row 조합)로 교체됨. Table 자체는 더 이상
    # correct_answer_widget에서 쓰이지 않는다.
    assert all(child["type"] != "Table" for child in payload["widget"]["children"])
    leaderboard_col = next(
        child for child in payload["widget"]["children"] if child["type"] == "Col"
    )
    assert len(leaderboard_col["children"]) == 5
    assert "나의 순위: 6위" in payload["copy_text"]


def test_correct_answer_widget_without_leaderboard() -> None:
    payload = correct_answer_widget("삼성전자", "가격", "순위", "재료", None, None, ["종료"])
    _assert_payload(payload, "correct_answer")
    assert all(child["type"] != "Table" for child in payload["widget"]["children"])


def test_leaderboard_table_rows_schema() -> None:
    """더 이상 correct_answer_widget에서 쓰이지 않지만, Table 스키마 자체는
    카카오 렌더러가 향후 지원할 경우를 대비해 보존·검증한다."""
    table = leaderboard_table_rows(_leaderboard())
    assert table["type"] == "Table"
    assert table["children"][0]["type"] == "Table.Row"
    assert table["children"][0]["header"] is True
    assert table["children"][0]["children"][0]["type"] == "Table.Cell"


def test_leaderboard_listview_rows_schema() -> None:
    col = leaderboard_listview_rows(_leaderboard())
    assert col["type"] == "Col"
    assert len(col["children"]) == 5
    first_row = col["children"][0]
    assert first_row["type"] == "Row"
    badge, name, score = first_row["children"]
    assert badge["type"] == "Badge" and badge["label"] == "1"
    assert badge["color"] == "warning"  # 1위는 warning 색
    assert name["flex"] == 1 and name["truncate"] is True
    assert score["textAlign"] == "end"


def test_to_content_text_preserves_korean() -> None:
    text = to_content_text({"한글": "그대로"})
    assert text == '{"한글": "그대로"}'
    assert json.loads(text) == {"한글": "그대로"}
