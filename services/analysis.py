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
