"""카카오 Tools 응답용 ChatKit 위젯 조립 함수."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts.schemas import LeaderboardSnapshot


def _text_lines(value: str, **properties: object) -> dict:
    """줄바꿈 문자열을 개별 Text로 나눈 Col을 만든다."""
    return {
        "type": "Col",
        "children": [
            {"type": "Text", "value": line, **properties}
            for line in value.splitlines()
        ],
    }


def _quiz_frame(
    quiz_id: str,
    mode_intro: str,
    body_children: list[dict],
    expires_in_sec: int,
    name: str,
    copy_body: str,
) -> dict:
    """3개 출제 모드가 공유하는 공통 틀. 바디만 모드별 함수가 채워 넣는다."""
    expires_in_min = expires_in_sec // 60
    children = [
        {
            "type": "Row",
            "align": "center",
            "gap": 6,
            "children": [
                {
                    "type": "Icon",
                    "name": "circle-question",
                    "color": "info",
                    "size": "md",
                },
                {
                    "type": "Badge",
                    "label": "난이도 중",
                    "color": "info",
                    "variant": "soft",
                    "size": "sm",
                },
            ],
        },
        {"type": "Spacer", "minSize": 8},
        {
            "type": "Title",
            "value": "주식대결 퀴즈",
            "size": "lg",
            "weight": "bold",
        },
        _text_lines(mode_intro, size="md", maxLines=3),
        {"type": "Divider", "spacing": 12},
        *body_children,
        {"type": "Divider", "spacing": 12},
        {"type": "Markdown", "value": f"정답 제출용 ID: `{quiz_id}`"},
        {
            "type": "Caption",
            "value": f"위 ID와 정답을 {expires_in_min}분 안에 함께 말해주세요",
            "size": "sm",
        },
    ]
    copy_text = (
        f"**주식대결 퀴즈**\n\n{mode_intro}\n\n{copy_body}\n\n"
        f"제출 ID: `{quiz_id}`"
    )
    return {
        "widget": {
            "type": "Card",
            "size": "full",
            "padding": 16,
            "children": children,
        },
        "copy_text": copy_text,
        "name": name,
    }


def price_quiz_widget(
    quiz_id: str,
    mode_intro: str,
    question_md: str,
    expires_in_sec: int = 1800,
) -> dict:
    """주가 퀴즈 출제 위젯. 숫자 입력을 강조하는 바디."""
    body = [
        {"type": "Markdown", "value": question_md},
        {
            "type": "Badge",
            "label": "숫자만 입력하세요",
            "color": "info",
            "variant": "soft",
            "size": "sm",
        },
    ]
    return _quiz_frame(
        quiz_id, mode_intro, body, expires_in_sec, "price_quiz", question_md
    )


def market_quiz_widget(
    quiz_id: str,
    mode_intro: str,
    question_md: str,
    change_pct: float,
    expires_in_sec: int = 1800,
) -> dict:
    """시장 퀴즈 출제 위젯. 등락률 방향에 따라 배지 색과 차트형 힌트를 다르게 준다."""
    direction_color = "success" if change_pct >= 0 else "danger"
    direction_label = f"{change_pct:+.2f}%"
    sparkline = _change_sparkline(change_pct)
    body = [
        {"type": "Markdown", "value": question_md},
        {
            "type": "Markdown",
            "value": f"**차트형 힌트** `{sparkline}`",
        },
        {
            "type": "Badge",
            "label": direction_label,
            "color": direction_color,
            "variant": "soft",
            "size": "sm",
        },
    ]
    return _quiz_frame(
        quiz_id,
        mode_intro,
        body,
        expires_in_sec,
        "market_quiz",
        f"{question_md}\n\n차트형 힌트: `{sparkline}`",
    )


def _change_sparkline(change_pct: float) -> str:
    """등락 방향과 강도를 한 줄 차트 모양으로 표현한다."""
    if change_pct >= 0:
        levels = "▁▂▃▄▅"
        arrow = "↗"
    else:
        levels = "▅▄▃▂▁"
        arrow = "↘"
    strength = min(5, max(1, int(abs(change_pct) // 2) + 1))
    return f"{levels} {arrow} {change_pct:+.2f}% · 강도 {strength}/5"


def company_quiz_widget(
    quiz_id: str,
    mode_intro: str,
    question_md: str,
    expires_in_sec: int = 1800,
) -> dict:
    """종목 퀴즈 출제 위젯. 힌트 목록(섹터/현재가/시총순위)을 그대로 표시."""
    body = [{"type": "Markdown", "value": question_md}]
    return _quiz_frame(
        quiz_id, mode_intro, body, expires_in_sec, "company_quiz", question_md
    )


def wrong_answer_widget(hint_text: str, attempts: int) -> dict:
    """오답 응답 위젯. 간단한 Card + Text 구성.
    {"widget": {...}, "copy_text": "...", "name": "wrong_answer"}"""
    copy_text = f"❌ 오답입니다. (시도 {attempts}회)\n\n💡 힌트: **{hint_text}**"
    return {
        "widget": {
            "type": "Card",
            "children": [
                {"type": "Text", "value": f"오답입니다. (시도 {attempts}회)"},
                {"type": "Badge", "label": hint_text, "color": "warning"},
            ],
        },
        "copy_text": copy_text,
        "name": "wrong_answer",
    }


def correct_answer_widget(
    answer_name: str,
    price_line: str,
    rank_line: str,
    reason_line: str,
    earned_score: int | None,
    leaderboard: "LeaderboardSnapshot | None",
    next_actions: list[str],
) -> dict:
    """정답 응답 위젯. 미니분석 + (있으면) 점수·TOP3 랭킹 + 다음 액션.
    {"widget": {...}, "copy_text": "...", "name": "correct_answer"}"""
    children: list[dict] = [
        {"type": "Title", "value": f"정답! {answer_name}", "size": "lg", "weight": "bold"},
        {"type": "Text", "value": price_line},
        {"type": "Text", "value": rank_line},
        {"type": "Text", "value": reason_line},
        {"type": "Divider", "spacing": 12},
    ]
    copy_lines = [
        f"✅ 정답! **{answer_name}**",
        "",
        "**미니분석**",
        f"- {price_line}",
        f"- {rank_line}",
        f"- {reason_line}",
    ]

    if leaderboard is not None:
        if earned_score is not None:
            children.append(
                {
                    "type": "Badge",
                    "label": f"이번 정답으로 {earned_score}점 획득!",
                    "color": "success",
                }
            )
            copy_lines.extend(["", f"🎯 이번 정답으로 **{earned_score}점** 획득!"])
        children.append(leaderboard_listview_rows(leaderboard))
        children.append(
            {
                "type": "Text",
                "value": f"내 점수 {leaderboard.my_entry.score}점 · {leaderboard.my_rank}위",
                "weight": "bold",
            }
        )
        copy_lines.extend(["", "**주간 TOP3**"])
        copy_lines.extend(
            f"{rank}. {entry.display_name} — {entry.score}점"
            for rank, entry in enumerate(leaderboard.top[:3], start=1)
        )
        copy_lines.append(
            f"내 점수 {leaderboard.my_entry.score}점 · {leaderboard.my_rank}위"
        )

    if next_actions:
        children.append({"type": "Divider", "spacing": 12})
        children.extend({"type": "Button", "label": action} for action in next_actions)
        copy_lines.extend(
            ["", "다음 중 선택: " + " / ".join(f"`{action}`" for action in next_actions)]
        )

    return {
        "widget": {"type": "Card", "size": "full", "padding": 16, "children": children},
        "copy_text": "\n".join(copy_lines),
        "name": "correct_answer",
    }


def with_leaderboard(
    payload: dict,
    leaderboard: "LeaderboardSnapshot | None",
    score_delta: int | None = None,
) -> dict:
    """기존 위젯 끝에 공통 주간 랭킹 패널을 붙인다.

    Preview에서 확인된 컴포넌트만 사용해 모든 응답의 하단 모양을 통일한다.
    """
    if leaderboard is None:
        return payload

    widget = dict(payload["widget"])
    children = list(widget.get("children", []))
    children.extend(
        [
            {"type": "Divider", "spacing": 12},
            {"type": "Title", "value": "주간 랭킹", "size": "md", "weight": "bold"},
            leaderboard_listview_rows(leaderboard),
            {
                "type": "Text",
                "value": f"내 점수 {leaderboard.my_entry.score}점 · {leaderboard.my_rank}위",
                "weight": "bold",
            },
        ]
    )
    widget["children"] = children

    copy_text = payload["copy_text"]
    ranking_lines = [""]
    if score_delta is not None:
        action = "획득" if score_delta > 0 else "감점"
        ranking_lines.append(f"점수 {abs(score_delta)}점 {action}")
        ranking_lines.append("")
    ranking_lines.append("**주간 TOP3**")
    ranking_lines.extend(
        f"{rank}. {entry.display_name} — {entry.score}점"
        for rank, entry in enumerate(leaderboard.top[:3], start=1)
    )
    ranking_lines.append(
        f"내 점수 {leaderboard.my_entry.score}점 · {leaderboard.my_rank}위"
    )
    return {
        "widget": widget,
        "copy_text": copy_text + "\n".join(ranking_lines),
        "name": payload["name"],
    }


def leaderboard_table_rows(leaderboard: "LeaderboardSnapshot") -> dict:
    """TOP3를 Table 컴포넌트로 조립한다.

    ⚠️ Preview 실측 결과(2026-08-19) Table 사용 시 정답 위젯 전체가 조용히
    일반 텍스트로 강등됨을 확인(HANDOFF.md 경고가 실측으로 확인됨). 더 이상
    correct_answer_widget에서 호출하지 않는다 — leaderboard_listview_rows 사용.
    이 함수는 향후 카카오 렌더러가 Table을 지원하게 될 경우를 대비해 보존한다.
    """

    def cell(value: str, *, align: str | None = None) -> dict:
        result: dict = {"type": "Table.Cell", "children": [{"type": "Text", "value": value}]}
        if align is not None:
            result["align"] = align
        return result

    rows = [
        {
            "type": "Table.Row",
            "header": True,
            "children": [cell("순위"), cell("닉네임"), cell("점수", align="end")],
        }
    ]
    rows.extend(
        {
            "type": "Table.Row",
            "children": [
                cell(str(rank)),
                cell(entry.display_name),
                cell(f"{entry.score}점", align="end"),
            ],
        }
        for rank, entry in enumerate(leaderboard.top[:3], start=1)
    )
    return {"type": "Table", "children": rows}


_TOP3_BADGE_COLORS = {1: "warning", 2: "secondary", 3: "info"}


def leaderboard_listview_rows(leaderboard: "LeaderboardSnapshot") -> dict:
    """TOP3를 Row 조합으로 조립한다(Card 안에 중첩되는 자식이라 ListView 루트는 쓰지 않음).

    HANDOFF.md §7 '리더보드 위젯 JSON' 샘플의 내부 Row 레이아웃(Badge+Text+Text)을
    가져오되, 감싸는 컨테이너는 ListView/ListViewItem(루트 전용·자식 전용으로 문서에
    명시됨 — Card 안에 중첩 시 스펙 위반 위험) 대신 이미 정답 위젯에서 검증된 Col로
    감싼다. 닉네임 Text에 flex:1+truncate:true를 줘야 점수가 오른쪽으로 밀리고 긴
    닉네임이 레이아웃을 안 깨뜨린다. 상위 3위는 Badge 색을 달리해 시각적 위계를 준다.
    """

    def row(rank: int, display_name: str, score: int) -> dict:
        badge_color = _TOP3_BADGE_COLORS.get(rank, "secondary")
        return {
            "type": "Row",
            "align": "center",
            "gap": 8,
            "children": [
                {
                    "type": "Badge",
                    "label": str(rank),
                    "color": badge_color,
                    "variant": "solid",
                    "pill": True,
                    "size": "sm",
                },
                {
                    "type": "Text",
                    "value": display_name,
                    "weight": "semibold",
                    "flex": 1,
                    "truncate": True,
                },
                {
                    "type": "Text",
                    "value": f"{score}점",
                    "weight": "bold",
                    "textAlign": "end",
                    "color": "success",
                },
            ],
        }

    rows = [
        row(rank, entry.display_name, entry.score)
        for rank, entry in enumerate(leaderboard.top[:3], start=1)
    ]
    return {"type": "Col", "gap": 6, "children": rows}


# ── 웰컴 / 모드 선택 안내 ─────────────────────────────────────


def welcome_widget() -> dict:
    """help 툴 응답 위젯. 서비스 소개 + 3모드 + 닉네임 필요성 + 발화 예시."""
    mode_rows = [
        {
            "type": "Row",
            "align": "center",
            "gap": 8,
            "children": [
                {"type": "Icon", "name": "chart", "color": "info", "size": "sm"},
                {"type": "Text", "value": "주가 — 현재가를 1만원 단위로 맞히기", "flex": 1},
            ],
        },
        {
            "type": "Row",
            "align": "center",
            "gap": 8,
            "children": [
                {"type": "Icon", "name": "analytics", "color": "info", "size": "sm"},
                {"type": "Text", "value": "시장 — 가장 오르거나 떨어진 종목 맞히기", "flex": 1},
            ],
        },
        {
            "type": "Row",
            "align": "center",
            "gap": 8,
            "children": [
                {"type": "Icon", "name": "sparkle", "color": "info", "size": "sm"},
                {"type": "Text", "value": "종목 — 섹터·가격·시총 힌트로 회사 맞히기", "flex": 1},
            ],
        },
    ]
    children = [
        {"type": "Title", "value": "주식대결에 오신 걸 환영해요!", "size": "lg", "weight": "bold"},
        {
            "type": "Text",
            "value": "코스피/코스닥 종목으로 즐기는 주식 퀴즈예요.",
            "size": "md",
        },
        {"type": "Divider", "spacing": 12},
        {"type": "Col", "gap": 8, "children": mode_rows},
        {"type": "Divider", "spacing": 12},
        {
            "type": "Text",
            "value": "닉네임을 알려주면 정답 시 주간 랭킹(매주 초기화)에 참여할 수 있어요.",
            "size": "sm",
        },
        {
            "type": "Caption",
            "value": '예: "주가 모드로 퀴즈 내줘. 닉네임은 찬희야."',
            "size": "sm",
        },
    ]
    copy_text = (
        "**주식대결에 오신 걸 환영해요!**\n\n"
        "코스피/코스닥 종목으로 즐기는 주식 퀴즈입니다.\n\n"
        "- 📈 주가 — 현재가를 1만원 단위로 맞히기\n"
        "- 📊 시장 — 가장 오르거나 떨어진 종목 맞히기\n"
        "- 🏢 종목 — 섹터·가격·시총 힌트로 회사 맞히기\n\n"
        "닉네임을 알려주면 정답 시 주간 랭킹(매주 초기화)에 참여할 수 있어요.\n\n"
        '예: "주가 모드로 퀴즈 내줘. 닉네임은 찬희야."'
    )
    return {
        "widget": {"type": "Card", "size": "full", "padding": 16, "children": children},
        "copy_text": copy_text,
        "name": "welcome",
    }


def mode_selection_widget() -> dict:
    """quiz가 mode/nickname 없이 호출됐을 때 반환하는 안내 위젯."""
    children = [
        {"type": "Text", "value": "모드와 닉네임을 알려주세요.", "weight": "semibold"},
        {
            "type": "Row",
            "gap": 6,
            "children": [
                {"type": "Badge", "label": "주가", "color": "info", "variant": "soft"},
                {"type": "Badge", "label": "시장", "color": "info", "variant": "soft"},
                {"type": "Badge", "label": "종목", "color": "info", "variant": "soft"},
            ],
        },
        {
            "type": "Caption",
            "value": '예: "종목 모드로 퀴즈 내줘. 닉네임은 찬희야."',
            "size": "sm",
        },
    ]
    copy_text = (
        "모드와 닉네임을 알려주세요.\n\n"
        "주가 / 시장 / 종목 중 하나를 골라주세요.\n\n"
        '예: "종목 모드로 퀴즈 내줘. 닉네임은 찬희야."'
    )
    return {
        "widget": {"type": "Card", "children": children},
        "copy_text": copy_text,
        "name": "mode_selection",
    }


# ── 안내 / 오류 위젯 (quiz_id 없는 경로) ───────────────────────


def _notice_widget(text: str, caption: str, name: str) -> dict:
    """짧은 안내/오류 응답 공통 틀. Text 한 줄 + Caption 한 줄."""
    children = [
        {"type": "Text", "value": text},
        {"type": "Caption", "value": caption, "size": "sm"},
    ]
    return {
        "widget": {"type": "Card", "children": children},
        "copy_text": f"{text}\n\n{caption}",
        "name": name,
    }


def already_solved_widget() -> dict:
    return _notice_widget(
        "🏁 이미 정답 처리된 퀴즈입니다.",
        "새 퀴즈를 출제해주세요.",
        "already_solved",
    )


def expired_quiz_widget() -> dict:
    return _notice_widget(
        "⏰ 만료된 퀴즈입니다.",
        "30분이 지나면 quiz_id가 사라져요. 새 퀴즈를 출제해주세요.",
        "expired_quiz",
    )


def quiz_not_found_widget() -> dict:
    return _notice_widget(
        "❓ 존재하지 않는 quiz_id입니다.",
        "quiz_id를 다시 확인해주세요.",
        "quiz_not_found",
    )


def us_blocked_widget() -> dict:
    return _notice_widget(
        "🌏 해외 종목 퀴즈는 준비 중입니다.",
        "지금은 국내(KR) 퀴즈만 즐길 수 있어요.",
        "us_blocked",
    )


def sector_empty_widget(sector_label: str) -> dict:
    return _notice_widget(
        f"🗂️ '{sector_label}' 섹터는 아직 준비된 종목이 부족해요.",
        "섹터를 비워두면 전체에서 출제해 드려요.",
        "sector_empty",
    )


def company_pool_empty_widget() -> dict:
    return _notice_widget(
        "🗂️ 회사 맞히기 데이터를 준비 중이에요.",
        "잠시 후 다시 시도해주세요.",
        "company_pool_empty",
    )


def mode_unknown_widget() -> dict:
    return _notice_widget(
        "주가 / 시장 / 종목 중에서 골라주세요.",
        '예: "주가 모드로 퀴즈 내줘."',
        "mode_unknown",
    )


def to_content_text(payload: dict) -> str:
    """한글을 보존해 위젯 payload를 JSON 문자열로 직렬화한다."""
    return json.dumps(payload, ensure_ascii=False)
