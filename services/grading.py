"""채점 + 힌트 선택.

- 종목명 정규화 판정(공백/대소문자/별칭)
- 국내 가격은 1만원 단위 반올림 버킷으로 판정
- 힌트: 출제 시 precompute된 것을 attempts 인덱스로 꺼낸다.
  price UP/DOWN만 제출값 의존이라 채점 시 비교 1회로 생성(루트 규칙 12 예외).

채점 경로에는 문자열 연산을 넣지 않는다(초성/힌트 생성은 quiz_bank 출제 시점에 끝냄).
"""

from __future__ import annotations

from contracts.schemas import Hint, Market, QuizState, QuizType, StockSnapshot

# US 활성화 시 기존 ±3% 판정을 유지한다. KR은 1만원 단위 버킷으로 판정한다.
PRICE_TOLERANCE = 0.03
PRICE_UNIT_KRW = 10_000
_EPS = 1e-9


def normalize_name(s: str) -> str:
    """종목명 정규화: 양끝 공백 제거, 모든 공백 제거, 대소문자 통일.

    한영 혼용/오타 흡수는 별칭 테이블(aliases)이 담당. 여기선 표기 정규화만.
    """
    return "".join(s.split()).casefold()


def resolve_alias(raw: str, aliases: dict[str, str]) -> str:
    """정규화된 제출값을 별칭 테이블로 표준명에 매핑. 없으면 정규화값 그대로.

    aliases 키는 정규화된 별칭, 값은 표준 종목명(정규화 전).
    """
    norm = normalize_name(raw)
    canonical = aliases.get(norm)
    return normalize_name(canonical) if canonical is not None else norm


def judge_name(state: QuizState, submitted: str, aliases: dict[str, str]) -> bool:
    """이름 맞추기 정답 판정. 제출값과 정답명을 정규화/별칭 해소 후 비교."""
    answer_norm = normalize_name(state.answer.name)
    submitted_norm = resolve_alias(submitted, aliases)
    return submitted_norm == answer_norm


def parse_price(submitted: str) -> float | None:
    """제출 문자열에서 가격 숫자 파싱. '8만원'은 80,000원으로 해석한다."""
    cleaned = (
        submitted.strip()
        .replace(",", "")
        .replace("$", "")
        .replace("%", "")
        .replace("￦", "")
    )
    multiplier = 1
    if "만" in cleaned:
        multiplier = PRICE_UNIT_KRW
        cleaned = cleaned.split("만", 1)[0]
    cleaned = cleaned.replace("원", "").strip()
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def _kr_price_bucket(value: float) -> int:
    """원화 가격을 가장 가까운 1만원 단위로 반올림한다."""
    return int((value + (PRICE_UNIT_KRW / 2)) // PRICE_UNIT_KRW)


def _submitted_kr_price_bucket(submitted: str) -> int | None:
    value = parse_price(submitted)
    if value is None:
        return None
    if "만" not in submitted and value < 1_000:
        value *= PRICE_UNIT_KRW
    return _kr_price_bucket(value)


def judge_price(answer: StockSnapshot, submitted: str) -> bool | None:
    """가격 판정. KR은 1만원 단위 버킷, 그 외 시장은 ±3%로 판정한다."""
    if answer.market == Market.KR:
        submitted_bucket = _submitted_kr_price_bucket(submitted)
        if submitted_bucket is None:
            return None
        return submitted_bucket == _kr_price_bucket(answer.price)

    value = parse_price(submitted)
    if value is None:
        return None
    if answer.price == 0:
        return value == 0
    rel = abs(value - answer.price) / answer.price
    return rel <= PRICE_TOLERANCE + _EPS


def is_correct(state: QuizState, submitted: str, aliases: dict[str, str]) -> bool:
    """퀴즈 타입에 따라 정답 여부 판정."""
    if state.quiz_type == QuizType.PRICE:
        return judge_price(state.answer, submitted) is True
    return judge_name(state, submitted, aliases)


def pick_hint(state: QuizState, submitted: str, attempt_index: int) -> Hint:
    """오답 힌트 선택. attempt_index는 1부터(첫 오답=1).

    - price: 제출가 대비 정답이 위/아래인지 1회 비교로 생성(precompute 불가).
    - 그 외: 출제 시 저장된 hints_precomputed에서 단계를 꺼낸다(문자열 연산 없음).
    """
    if state.quiz_type == QuizType.PRICE:
        if state.answer.market == Market.KR:
            submitted_bucket = _submitted_kr_price_bucket(submitted)
            if submitted_bucket is None:
                return Hint(kind="updown", text="숫자로 입력해주세요")
            direction = (
                "UP" if _kr_price_bucket(state.answer.price) > submitted_bucket else "DOWN"
            )
            return Hint(kind="updown", text=direction)

        value = parse_price(submitted)
        if value is None:
            return Hint(kind="updown", text="숫자로 입력해주세요")
        direction = "UP" if state.answer.price > value else "DOWN"
        return Hint(kind="updown", text=direction)

    hints = state.hints_precomputed
    if not hints:
        return Hint(kind="none", text="힌트가 없습니다")
    idx = min(attempt_index - 1, len(hints) - 1)
    return hints[max(0, idx)]
