"""전 모듈이 공유하는 유일한 계약. 이 파일은 읽기 전용이다.

수정이 필요하면 세션을 멈추고 사용자에게 변경을 제안한다.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ── 공통 enum ──────────────────────────────────────────────


class Market(str, Enum):
    KR = "KR"
    US = "US"


class Period(str, Enum):
    """등락률 랭킹 기간."""

    TODAY = "today"
    YESTERDAY = "yesterday"
    WEEK = "1w"
    MONTH = "1m"
    YEAR = "1y"


class QuizType(str, Enum):
    PRICE = "price"            # 가격 맞추기 (업다운 힌트)
    GAINER = "gainer"          # 많이 오른 종목 맞추기
    LOSER = "loser"            # 많이 떨어진 종목 맞추기
    COMPANY = "company"        # 가격+섹터 힌트로 회사 맞추기 (초성 힌트)


class Verdict(str, Enum):
    CORRECT = "correct"
    WRONG = "wrong"
    EXPIRED = "expired"        # quiz_id TTL 만료
    NOT_FOUND = "not_found"    # 존재하지 않는 quiz_id


class Sector(str, Enum):
    """guess_company 풀: 대표 섹터 10개 (각 시총 TOP10 = 100종목)."""

    SEMICONDUCTOR = "반도체"
    BATTERY = "2차전지"
    BIO = "바이오"
    AUTO = "자동차"
    FINANCE = "금융"
    INTERNET_GAME = "인터넷게임"
    ENTERTAINMENT = "엔터"
    SHIPBUILDING_DEFENSE = "조선방산"
    CHEMICAL_STEEL = "화학철강"
    RETAIL_FOOD = "유통식품"


# ── 데이터 스냅샷 (clients / batch 산출) ─────────────────────


class StockSnapshot(BaseModel):
    """시세 스냅샷. 퀴즈 출제 시점에 고정되어 저장된다 (실시간 아님)."""

    ticker: str
    name: str
    market: Market
    sector: Sector | None = None
    price: float                      # 출제 시점 가격 (KR: 원, US: 달러)
    change_pct: float                 # 해당 기간 등락률 (%)
    market_cap_rank: int | None = None
    as_of: datetime                   # 데이터 기준 시각


class RankingItem(BaseModel):
    """등락률 랭킹 1행 (batch 산출 JSON의 단위)."""

    rank: int
    snapshot: StockSnapshot
    period: Period


# ── 퀴즈 상태 (store 저장 단위) ──────────────────────────────


class Hint(BaseModel):
    """오답 시 반환되는 힌트."""

    kind: str                         # "updown" | "chosung" | "first_letter"
    text: str                         # 예: "UP", "ㅅㅅㅈㅈ", "T____ (5글자)"


class QuizState(BaseModel):
    """quiz_id 로 저장되는 서버 측 정답 상태. TTL 30분."""

    quiz_id: str
    quiz_type: QuizType
    answer: StockSnapshot             # 정답 종목 (가격퀴즈면 price가 정답값)
    hints_precomputed: list[Hint] = Field(default_factory=list)
    # 출제 시점에 힌트 전 단계(초성→첫글자 등)를 미리 생성해 저장한다.
    # 채점 경로에서 문자열 연산을 없애기 위함 (속도 원칙).
    # 단, price_quiz의 UP/DOWN은 제출값 의존이라 채점 시 비교 1회로 생성.
    attempts: int = 0                 # 시도 횟수 (힌트 단계 결정)
    created_at: datetime
    solved: bool = False


class ScoreEntry(BaseModel):
    """랭킹 1인분 기록. 주간 리셋 시 전원 초기화된다.

    identity_key는 nickname 또는 (향후 OAuth 도입 시) OAuth user_id.
    현재는 nickname만 사용하며, 서로 다른 식별 수단은 별도 키로 취급한다.
    """

    identity_key: str
    display_name: str
    score: int = 0
    updated_at: datetime


class LeaderboardSnapshot(BaseModel):
    """submit_answer 응답에 실리는 랭킹 요약. 정답 확정 시에만 생성된다."""

    top: list[ScoreEntry]
    my_entry: ScoreEntry
    my_rank: int
    week_started_at: datetime


class Reason(BaseModel):
    """프리캐싱된 원인 팩트. 배치 전용 산출물.

    source_url 없이 이 객체를 만드는 것 자체가 금지다 (환각 구조적 차단).
    런타임에서 Reason이 조회되지 않으면 reason_line은
    반드시 "특별한 재료 확인 안 됨" 이다.
    """

    ticker: str
    text: str                         # 팩트 1줄
    source_url: str                   # 필수
    published_at: datetime


# ── 툴 응답 (services 산출, server가 마크다운으로 조립) ───────


class QuizQuestion(BaseModel):
    """출제 툴 4종의 공통 반환. 정답은 절대 포함하지 않는다."""

    quiz_id: str
    quiz_type: QuizType
    question_md: str                  # 문제 본문 (마크다운)
    hint_policy: str                  # "updown" | "chosung"
    expires_in_sec: int = 1800


class MiniAnalysis(BaseModel):
    """정답 후 미니분석. 팩트만 담는다. 권유 문장 금지."""

    price_line: str                   # 현재가/등락률 1줄
    rank_line: str                    # 시총 순위 1줄
    reason_line: str                  # 기간 내 원인 팩트 1줄.
    # 근거 없으면 반드시: "특별한 재료 확인 안 됨"


class GradingResult(BaseModel):
    """submit_answer 반환."""

    verdict: Verdict
    hint: Hint | None = None          # WRONG일 때만
    analysis: MiniAnalysis | None = None  # CORRECT일 때만
    next_actions: list[str] = Field(
        default=["이 종목 더 분석", "다음 퀴즈"]
    )  # CORRECT일 때 2택 분기
    attempts: int = 0
