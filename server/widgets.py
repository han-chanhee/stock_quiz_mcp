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


def quiz_question_widget(
    quiz_id: str,
    mode_intro: str,
    question_md: str,
    expires_in_sec: int = 1800,
) -> dict:
    """출제 응답 위젯. HANDOFF.md §7 '퀴즈 출제 위젯 JSON' 스펙을 따른다.
    반환값은 {"widget": {...}, "copy_text": "...", "name": "quiz_question"} 형태."""
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
        {"type": "Markdown", "value": question_md},
        {"type": "Markdown", "value": f"정답 제출용 ID: `{quiz_id}`"},
        {
            "type": "Caption",
            "value": f"위 ID와 정답을 {expires_in_min}분 안에 함께 말해주세요",
            "size": "sm",
        },
    ]
    copy_text = (
        f"**주식대결 퀴즈**\n\n{mode_intro}\n\n{question_md}\n\n"
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
        "name": "quiz_question",
    }


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
    """정답 응답 위젯. 미니분석 + (있으면) 점수·TOP5 랭킹 + 다음 액션.
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
        children.append(leaderboard_table_rows(leaderboard))
        children.append(
            {"type": "Text", "value": f"나의 순위: {leaderboard.my_rank}위", "weight": "bold"}
        )
        copy_lines.extend(["", "**주간 TOP5**"])
        copy_lines.extend(
            f"{rank}. {entry.display_name} — {entry.score}점"
            for rank, entry in enumerate(leaderboard.top[:5], start=1)
        )
        copy_lines.append(f"나의 순위: {leaderboard.my_rank}위")

    if next_actions:
        children.append({"type": "Divider", "spacing": 12})
        # TEMP: 딥링크 실측용. 정식 코드 아님 — 확인 끝나면 원상복구.
        _TEMP_DEEPLINK_TESTS = [
            ("[실측1] kakaotalk 스킴", "kakaotalk://msg/text?text=주가"),
            ("[실측2] kakaolink 스킴", "kakaolink://send?text=주가"),
        ]
        children.extend(
            {
                "type": "Button",
                "label": label,
                "onClickAction": {"payload": {"target": {"url": url}}},
            }
            for label, url in _TEMP_DEEPLINK_TESTS
        )
        children.extend({"type": "Button", "label": action} for action in next_actions)
        copy_lines.extend(
            ["", "다음 중 선택: " + " / ".join(f"`{action}`" for action in next_actions)]
        )

    return {
        "widget": {"type": "Card", "size": "full", "padding": 16, "children": children},
        "copy_text": "\n".join(copy_lines),
        "name": "correct_answer",
    }


def leaderboard_table_rows(leaderboard: "LeaderboardSnapshot") -> dict:
    """TOP5를 Table 컴포넌트로 조립한다."""

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
        for rank, entry in enumerate(leaderboard.top[:5], start=1)
    )
    return {"type": "Table", "children": rows}


def to_content_text(payload: dict) -> str:
    """한글을 보존해 위젯 payload를 JSON 문자열로 직렬화한다."""
    return json.dumps(payload, ensure_ascii=False)
