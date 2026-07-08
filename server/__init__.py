"""모듈 E: FastMCP 조립 계층.

주의: main.py는 fastmcp(Python 3.10+)를 import한다. fastmcp 없는 환경에서
cache/handlers만 쓰려면 하위 모듈을 직접 import한다(from server.cache import ...).
"""

from .cache import DataValidationError, QuizCache
from .handlers import QuizHandlers, QuizMode, QuizOutcome, SubmitOutcome

__all__ = [
    "QuizCache",
    "DataValidationError",
    "QuizHandlers",
    "QuizMode",
    "QuizOutcome",
    "SubmitOutcome",
]
