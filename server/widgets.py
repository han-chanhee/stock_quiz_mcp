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
    # TEMP: 딥링크 실측용. 오답은 재현이 빨라(아무 숫자나 입력) 여기서 즉시 확인한다.
    # [0]은 대조군(정상 https URL) — 이게도 강등되면 커스텀 스킴이 아니라 버튼 개수/
    # 다른 요인이 원인이라는 뜻. 확인 끝나면 원상복구.
    deeplink_buttons = [
        {
            "type": "Button",
            "label": label,
            "onClickAction": {"payload": {"target": {"url": url}}},
        }
        for label, url in (
            ("[0 대조군] https", "https://kakao.com"),
            ("[1] kakaotalk scheme", "kakaotalk://msg/text?text=주가"),
            ("[2] kakaolink scheme", "kakaolink://send?text=주가"),
            ("[3] kakaoopen scheme", "kakaoopen://send?text=주가"),
            ("[4] sms scheme", "sms:?body=주가"),
            ("[5] intent scheme", "intent://send?text=주가#Intent;end"),
            ("[6] kakaotalk share", "kakaotalk://share?text=주가"),
        )
    ]
    # [7]~[10] ChatKit onAction/sendUserMessage 방식 실측 — 카카오 공식 문서엔
    # target.url만 명시돼 근거 없음. 카카오가 handler:"client" 액션을 자체
    # onAction으로 가로채 sendUserMessage처럼 처리하는지 확인용 필드 조합 4종.
    quick_reply_buttons = [
        {"type": "Button", "label": label, "onClickAction": action}
        for label, action in (
            (
                "[7] payload.text + handler:client",
                {
                    "type": "quick_reply",
                    "handler": "client",
                    "payload": {"text": "주가 정답을 다시 볼래"},
                },
            ),
            (
                "[8] payload.message",
                {"type": "quick_reply", "payload": {"message": "종목 힌트 더 줘"}},
            ),
            (
                "[9] OpenAI 샘플 형태(cats.more_names 스타일)",
                {
                    "type": "quiz.more_hint",
                    "handler": "client",
                    "payload": {},
                },
            ),
            (
                "[10] target.text(경로 변형)",
                {"payload": {"target": {"text": "힌트 더 줘"}}},
            ),
        )
    ]
    return {
        "widget": {
            "type": "Card",
            "children": [
                {"type": "Text", "value": f"오답입니다. (시도 {attempts}회)"},
                {"type": "Badge", "label": hint_text, "color": "warning"},
                *deeplink_buttons,
                *quick_reply_buttons,
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
        children.append(leaderboard_listview_rows(leaderboard))
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
    """TOP5를 Table 컴포넌트로 조립한다.

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
        for rank, entry in enumerate(leaderboard.top[:5], start=1)
    )
    return {"type": "Table", "children": rows}


_TOP3_BADGE_COLORS = {1: "warning", 2: "secondary", 3: "info"}


def leaderboard_listview_rows(leaderboard: "LeaderboardSnapshot") -> dict:
    """TOP5를 Row 조합으로 조립한다(Card 안에 중첩되는 자식이라 ListView 루트는 쓰지 않음).

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
        for rank, entry in enumerate(leaderboard.top[:5], start=1)
    ]
    return {"type": "Col", "gap": 6, "children": rows}


def to_content_text(payload: dict) -> str:
    """한글을 보존해 위젯 payload를 JSON 문자열로 직렬화한다."""
    return json.dumps(payload, ensure_ascii=False)
