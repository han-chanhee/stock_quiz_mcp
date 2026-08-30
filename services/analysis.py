"""정답 후 미니분석 생성. 팩트만. 매수/매도 권유 문장 절대 금지.

문장 템플릿을 코드에 고정한다(생성형 문장 금지). reason은 프리캐싱된 Reason만 사용하고,
없으면 반드시 "특별한 재료 확인 안 됨"을 반환한다(루트 규칙 8·11).
"""

from __future__ import annotations

from contracts.schemas import Market, MiniAnalysis, Reason, StockSnapshot

NO_REASON = "특별한 재료 확인 안 됨"


def _fmt_price(snap: StockSnapshot) -> str:
    if snap.market == Market.KR:
        return f"{snap.price:,.0f}원"
    return f"${snap.price:,.2f}"


def _fmt_pct(pct: float) -> str:
    sign = "+" if pct > 0 else ""  # 음수는 자체 부호
    return f"{sign}{pct:.2f}%"


def _sector_label(snap: StockSnapshot) -> str:
    return snap.sector.value if snap.sector is not None else "미분류"


def _price_band(snap: StockSnapshot) -> str:
    if snap.market == Market.US:
        if snap.price < 20:
            return "$20 미만"
        if snap.price < 100:
            return "$20~$100"
        return "$100 이상"
    if snap.price < 5_000:
        return "5천원 미만"
    if snap.price < 30_000:
        return "5천~3만원"
    if snap.price < 100_000:
        return "3만~10만원"
    return "10만원 이상"


def _movement_label(change_pct: float) -> str:
    if change_pct >= 5:
        return "강한 상승"
    if change_pct > 0:
        return "상승"
    if change_pct <= -5:
        return "강한 하락"
    if change_pct < 0:
        return "하락"
    return "보합"


def _rank_hint_line(snap: StockSnapshot) -> str:
    if snap.market_cap_rank is None:
        return "순위형 단서는 이번 데이터에 없습니다."
    return f"데이터 랭킹 단서는 {snap.market_cap_rank}위권입니다."


def _variant_key(snap: StockSnapshot, salt: str) -> int:
    return (sum(ord(ch) for ch in f"{snap.ticker}:{salt}") % 3)


def _feature_line(
    answer: StockSnapshot,
    reason: Reason | None,
    *,
    reveal_name: bool,
) -> str:
    if reason is None or not reason.source_url:
        return "검색 기반 특징은 아직 확인된 공개 재료가 없습니다."
    text = reason.text.strip()
    if not reveal_name:
        text = text.replace(answer.name, "해당 종목")
        compact_name = answer.name.replace(" ", "")
        text = text.replace(compact_name, "해당 종목")
    return f"검색 기반 특징: {text}"


def build_question_analysis(
    answer: StockSnapshot,
    context: str,
    reason: Reason | None = None,
) -> list[str]:
    """출제 위젯용 5줄 분석. 이름 맞히기 계열은 정답명을 노출하지 않는다."""
    movement = _movement_label(answer.change_pct)
    pct = _fmt_pct(answer.change_pct)
    sector = _sector_label(answer)
    price_band = _price_band(answer)
    rank_line = _rank_hint_line(answer)
    variant = _variant_key(answer, context)

    if context == "price":
        feature = _feature_line(answer, reason, reveal_name=True)
        variants = [
            [
                f"{answer.name}은 출제 시점 기준 {movement} 흐름({pct})입니다.",
                f"가격대는 {price_band}이고 원 단위 숫자로 맞히면 됩니다.",
                f"섹터는 {sector}로 분류되어 있습니다.",
                rank_line,
                feature,
            ],
            [
                f"이번 문제는 {answer.name}의 현재가 감각을 묻습니다.",
                f"등락률 단서는 {pct}, 흐름은 {movement}입니다.",
                f"대략적인 가격 구간은 {price_band}입니다.",
                rank_line,
                feature,
            ],
            [
                f"{answer.name}의 가격을 맞히는 공개 종목형 문제입니다.",
                f"출제 데이터의 움직임은 {movement}({pct})입니다.",
                f"섹터 단서는 {sector}, 가격대 단서는 {price_band}입니다.",
                rank_line,
                feature,
            ],
        ]
        return variants[variant]

    feature = _feature_line(answer, reason, reveal_name=False)
    label = {
        "market": "시장 랭킹",
        "company": "종목 추론",
        "chart": "차트 추론",
    }.get(context, "종목 추론")
    variants = [
        [
            f"이 문제는 정답명을 숨긴 {label} 문제입니다.",
            f"등락 흐름은 {movement}({pct})입니다.",
            f"가격대는 {price_band}, 섹터는 {sector}입니다.",
            rank_line,
            feature,
        ],
        [
            f"정답 종목명은 아직 공개하지 않습니다. 유형은 {label}입니다.",
            f"움직임 단서는 {movement}, 등락률은 {pct}입니다.",
            f"가격 구간은 {price_band}이고 섹터 단서는 {sector}입니다.",
            rank_line,
            feature,
        ],
        [
            f"{label} 문제라서 이름보다 패턴과 단서를 먼저 봐야 합니다.",
            f"출제 시점 흐름은 {movement}({pct})입니다.",
            f"가격대 {price_band}, 섹터 {sector}가 핵심 단서입니다.",
            rank_line,
            feature,
        ],
    ]
    return variants[variant]


def build_answer_analysis_lines(
    answer: StockSnapshot,
    reason: Reason | None = None,
) -> list[str]:
    """정답 공개 후 보여줄 5줄 분석. 팩트 기반 문장만 조립한다."""
    reason_text = reason.text if reason is not None and reason.source_url else NO_REASON
    return [
        f"{answer.name} 현재가는 {_fmt_price(answer)}이고 등락률은 {_fmt_pct(answer.change_pct)}입니다.",
        f"출제 시점 기준 흐름은 {_movement_label(answer.change_pct)}으로 분류됩니다.",
        f"섹터는 {_sector_label(answer)}, 가격대는 {_price_band(answer)}입니다.",
        _rank_hint_line(answer),
        f"확인된 재료: {reason_text}",
    ]


def build_analysis(answer: StockSnapshot, reason: Reason | None = None) -> MiniAnalysis:
    """스냅샷(+선택적 Reason)으로 팩트 3줄 조립."""
    price_line = f"{answer.name} 현재가 {_fmt_price(answer)} ({_fmt_pct(answer.change_pct)})"

    if answer.market_cap_rank is not None:
        market_label = "국내" if answer.market == Market.KR else "미국"
        rank_line = f"{market_label} 시가총액 {answer.market_cap_rank}위"
    else:
        rank_line = "시가총액 순위 정보 없음"

    if reason is not None and reason.source_url:
        reason_line = reason.text
    else:
        reason_line = NO_REASON

    return MiniAnalysis(
        price_line=price_line,
        rank_line=rank_line,
        reason_line=reason_line,
    )
