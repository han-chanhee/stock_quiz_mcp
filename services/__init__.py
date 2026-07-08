"""모듈 B: 퀴즈 출제/채점/힌트/미니분석 로직."""

from .analysis import NO_REASON, build_analysis
from .grading import (
    PRICE_TOLERANCE,
    is_correct,
    judge_name,
    judge_price,
    normalize_name,
    parse_price,
    pick_hint,
    resolve_alias,
)
from .hangul import chosung, chosung_hint, first_letter_hint, first_two_hint
from .quiz_bank import QuizBank

__all__ = [
    "QuizBank",
    "is_correct",
    "judge_name",
    "judge_price",
    "parse_price",
    "normalize_name",
    "resolve_alias",
    "pick_hint",
    "PRICE_TOLERANCE",
    "build_analysis",
    "NO_REASON",
    "chosung",
    "chosung_hint",
    "first_letter_hint",
    "first_two_hint",
]
