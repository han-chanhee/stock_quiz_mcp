"""한글 초성/힌트 유틸. 외부 라이브러리 금지 — 유니코드 직접 분해.

출제 시점에 힌트 전 단계를 미리 만들어 QuizState.hints_precomputed에 저장하기 위한 함수들.
채점 경로에서는 이 함수들을 호출하지 않는다(저장된 힌트를 꺼내 쓰기만).
"""

from __future__ import annotations

_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_CHOSUNG_COUNT = 588  # 21(중성) * 28(종성)

# 초성 19자
_CHOSUNG = [
    "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]


def chosung(text: str) -> str:
    """문자열의 초성 추출. 한글 음절만 초성으로 바꾸고 그 외 문자는 그대로 둔다.

    예) "삼성전자" -> "ㅅㅅㅈㅈ"
        "SK하이닉스" -> "SKㅎㅇㄴㅅ"
    """
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if _HANGUL_BASE <= code <= _HANGUL_LAST:
            lead = (code - _HANGUL_BASE) // _CHOSUNG_COUNT
            out.append(_CHOSUNG[lead])
        else:
            out.append(ch)
    return "".join(out)


def chosung_hint(name: str) -> str:
    """1단계(약한) 힌트. 한글은 초성으로, 라틴/숫자는 '_'로 마스킹한다.

    chosung()과 달리 비한글 문자를 그대로 노출하지 않는다 — KR 시장의 라틴 종목명
    (예: "NAVER", "JYP Ent.")에서 정답이 통째로 새는 것을 막기 위함(회귀 방지).
    공백/기호는 글자 수 감을 주도록 유지한다.

    예) "삼성전자" -> "ㅅㅅㅈㅈ"
        "SK하이닉스" -> "__ㅎㅇㄴㅅ"
        "NAVER" -> "_____"
        "JYP Ent." -> "___ ___."
    """
    out: list[str] = []
    for ch in name:
        code = ord(ch)
        if _HANGUL_BASE <= code <= _HANGUL_LAST:
            lead = (code - _HANGUL_BASE) // _CHOSUNG_COUNT
            out.append(_CHOSUNG[lead])
        elif ch.isalnum():
            out.append("_")  # 라틴/숫자 마스킹 — 정답 유출 방지
        else:
            out.append(ch)  # 공백/기호 유지
    return "".join(out)


def first_letter_hint(name: str) -> str:
    """첫 글자 + 나머지 밑줄 + 글자 수.

    예) "삼성전자" -> "삼___ (4글자)"
        "Tesla"    -> "T____ (5글자)"
    """
    n = len(name)
    if n == 0:
        return "(빈 이름)"
    if n == 1:
        return f"{name} (1글자)"
    return f"{name[0]}{'_' * (n - 1)} ({n}글자)"


def first_two_hint(name: str) -> str:
    """앞 두 글자 공개(마지막 단계 힌트). 예) "삼성전자" -> "삼성__ (4글자)"."""
    n = len(name)
    if n <= 2:
        return first_letter_hint(name)
    return f"{name[:2]}{'_' * (n - 2)} ({n}글자)"
