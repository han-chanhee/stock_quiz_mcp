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
    with_leaderboard,
    wrong_answer_widget,
)


def _walk_components(value: object):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_components(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_components(item)


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
    if payload["widget"]["type"] == "Card":
        children = payload["widget"]["children"]
        assert children[0]["type"] == "Col"
        assert children[1]["type"] == "Divider"
        serialized_header = json.dumps(children[0], ensure_ascii=False)
        assert "/assets/logo-banner.png" in serialized_header
        assert '"type": "Markdown"' in serialized_header
        assert all(
            child.get("type") not in {"Box", "Image"}
            for child in _walk_components(payload["widget"])
        )
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
    assert any(child.get("type") == "Col" for child in _walk_components(payload["widget"]))
    assert "QZ-한글" in payload["copy_text"]


def test_price_quiz_widget_uses_mode_independent_title_and_question() -> None:
    question_md = "**삼성전자**의 현재 주가는 얼마일까요?"
    payload = price_quiz_widget("QZ-1", "📈 주가 퀴즈 — 가격 맞히기", question_md)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "이 기업의 종목명은?" not in serialized
    assert question_md in serialized


def test_quiz_widget_keeps_analysis_in_simple_card() -> None:
    analysis = [f"문제 분석 {index}" for index in range(1, 6)]
    payload = price_quiz_widget(
        "QZ-A",
        "📈 주가 퀴즈 — 가격 맞히기",
        "현재가는?",
        analysis_lines=analysis,
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    _assert_payload(payload, "price_quiz")
    assert "문제 분석" in serialized
    assert all(line in serialized for line in analysis)
    assert "Box" not in serialized
    assert "Image" not in serialized


def test_market_quiz_widget_direction_badge_color() -> None:
    up = market_quiz_widget("QZ-2", "📊 시장 퀴즈", "가장 오른 종목은?", 5.2)
    down = market_quiz_widget("QZ-3", "📊 시장 퀴즈", "가장 떨어진 종목은?", -3.1)
    _assert_payload(up, "market_quiz")
    _assert_payload(down, "market_quiz")
    up_badge = next(
        c
        for c in _walk_components(up["widget"])
        if c.get("type") == "Badge" and c.get("label") == "+5.20%"
    )
    down_badge = next(
        c
        for c in _walk_components(down["widget"])
        if c.get("type") == "Badge" and c.get("label") == "-3.10%"
    )
    assert up_badge["color"] == "success"
    assert down_badge["color"] == "danger"
    assert "차트형 힌트" in up["copy_text"]
    assert "▁▂▃" in up["copy_text"]
    assert "▅▄▃" in down["copy_text"]


def test_company_quiz_widget_payload() -> None:
    question_md = "- 섹터: **반도체**\n- 현재가: 80,000원\n- 시총 1위권"
    payload = company_quiz_widget("QZ-4", "🏢 종목 퀴즈", question_md)
    _assert_payload(payload, "company_quiz")
    assert "QZ-4" in payload["copy_text"]


def test_all_quiz_widgets_include_chart_hint() -> None:
    cases = [
        ("QZ-P", price_quiz_widget("QZ-P", "📈 주가 퀴즈", "현재가는?")),
        ("QZ-M", market_quiz_widget("QZ-M", "📊 시장 퀴즈", "가장 오른 종목은?", 5.2)),
        ("QZ-C", company_quiz_widget("QZ-C", "🏢 종목 퀴즈", "이 회사는?")),
    ]

    for quiz_id, payload in cases:
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "차트 힌트" in serialized
        assert f"/quiz/chart/{quiz_id}.png" in serialized
        assert "![차트 힌트]" in serialized
        assert "차트 힌트:" in payload["copy_text"]


def test_welcome_widget_payload() -> None:
    payload = welcome_widget()
    _assert_payload(payload, "welcome")
    assert "닉네임" in payload["copy_text"]
    assert "차트 —" not in payload["copy_text"]


def test_mode_selection_widget_payload() -> None:
    payload = mode_selection_widget()
    _assert_payload(payload, "mode_selection")
    assert "모드" in payload["copy_text"]
    assert "차트" not in payload["copy_text"]


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
    warning_badge = next(
        child for child in _walk_components(payload["widget"]) if child.get("type") == "Badge"
    )
    assert warning_badge["color"] == "warning"


def test_correct_answer_widget_payload_and_top_three() -> None:
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
    assert all(child.get("type") != "Table" for child in _walk_components(payload))
    leaderboard_col = next(
        child
        for child in _walk_components(payload["widget"])
        if child["type"] == "Col" and len(child.get("children", [])) == 3
    )
    assert len(leaderboard_col["children"]) == 3
    assert "주간 TOP3" in payload["copy_text"]
    assert "내 점수 54점 · 6위" in payload["copy_text"]


def test_correct_answer_widget_without_leaderboard() -> None:
    payload = correct_answer_widget("삼성전자", "가격", "순위", "재료", None, None, ["종료"])
    _assert_payload(payload, "correct_answer")
    assert all(child.get("type") != "Table" for child in _walk_components(payload))


def test_correct_answer_widget_uses_answer_analysis_lines() -> None:
    analysis = [f"정답 분석 {index}" for index in range(1, 6)]
    payload = correct_answer_widget(
        "삼성전자",
        "가격",
        "순위",
        "재료",
        None,
        None,
        [],
        analysis_lines=analysis,
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    _assert_payload(payload, "correct_answer")
    assert "정답 분석" in serialized
    assert all(line in serialized for line in analysis)
    assert "가격" not in payload["copy_text"]


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
    assert len(col["children"]) == 3
    first_row = col["children"][0]
    assert first_row["type"] == "Row"
    badge, name, score = first_row["children"]
    assert badge["type"] == "Badge" and badge["label"] == "1"
    assert badge["color"] == "warning"  # 1위는 warning 색
    assert name["flex"] == 1 and name["truncate"] is True
    assert score["textAlign"] == "end"


def test_with_leaderboard_appends_common_ranking_panel() -> None:
    payload = price_quiz_widget("QZ-5", "📈 주가 퀴즈", "현재가는?")
    combined = with_leaderboard(payload, _leaderboard())
    _assert_payload(combined, "price_quiz")

    assert combined["widget"] is not payload["widget"]
    assert "주간 TOP3" in combined["copy_text"]
    assert "내 점수" in combined["copy_text"]
    assert any(
        child.get("type") == "Title" and child.get("value") == "주간 랭킹"
        for child in _walk_components(combined["widget"])
    )


def test_to_content_text_preserves_korean() -> None:
    text = to_content_text({"한글": "그대로"})
    assert text == '{"한글": "그대로"}'
    assert json.loads(text) == {"한글": "그대로"}
