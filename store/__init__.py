"""모듈 C: quiz_id 인메모리 TTL 스토어."""

from .quiz_store import (
    DEFAULT_MAX_ENTRIES,
    DEFAULT_TTL_SEC,
    QuizStore,
    new_quiz_id,
)

__all__ = ["QuizStore", "new_quiz_id", "DEFAULT_TTL_SEC", "DEFAULT_MAX_ENTRIES"]
