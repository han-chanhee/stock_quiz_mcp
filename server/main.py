"""모듈 E: FastMCP 엔트리. 툴 5개 등록 + 의존성 주입 지점 build_app 하나.

Streamable HTTP, stateless_http=True.
※ MCP 프로토콜 세션은 미사용(stateless). 퀴즈 상태(quiz_id)는 QuizStore가 별도로
   보관한다 — 프로토콜 세션과 앱 상태는 별개다.

의존성 주입은 build_app(cache, store, bank, refresh_client) 한 곳에서 끝난다.
테스트/조립에서 mock↔실구현 교체가 이 함수 하나로 완결된다.

주의: 이 모듈은 fastmcp 패키지(Python 3.10+)를 import한다. 순수 오케스트레이션 로직은
handlers.py/cache.py에 있으며 fastmcp 없이 테스트된다(tests/test_server.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import functools
import os
from collections.abc import AsyncIterator
from datetime import timezone
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from contracts.schemas import Market, Period, Sector

from clients.base import MarketClient
from services.quiz_bank import QuizBank
from store import QuizStore

from .cache import QuizCache
from .handlers import QuizHandlers, QuizMode

_KST = timezone(_dt.timedelta(hours=9))
_DATA_DIR = Path(__file__).resolve().parent.parent / "batch" / "data"

# 장중 리프레셔 시각(KST). 5분 폴링 금지 — 정확히 3회만.
_REFRESH_TIMES = [(10, 0), (13, 0), (15, 40)]

_COMMON_ANN = dict(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
    idempotentHint=False,
)

_SAFE_ERROR = "잠시 후 다시 시도해주세요."


def build_app(
    cache: QuizCache,
    store: QuizStore,
    bank: QuizBank | None = None,
    refresh_client: MarketClient | None = None,
) -> FastMCP:
    """단일 의존성 주입 지점. 여기서 cache/store/bank(/리프레셔 클라이언트)를 꽂는다."""
    handlers = QuizHandlers(cache, store, bank)

    @contextlib.asynccontextmanager
    async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
        # 리프레셔 클라이언트가 주입된 경우에만 장중 3회 갱신 태스크를 띄운다.
        task = None
        if refresh_client is not None:
            task = asyncio.create_task(_refresher_loop(cache, refresh_client))
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    # stateless_http는 run()/http_app() 시점에 전달한다(FastMCP 3.x API).
    # MCP 프로토콜 세션 미사용 — quiz 상태는 QuizStore가 별도로 보관한다.
    mcp = FastMCP(name="stock-quiz-dictionary", lifespan=_lifespan)

    def _safe(fn):
        """스택트레이스 노출 금지: 예외를 정제 한 줄로 치환.

        functools.wraps로 __wrapped__를 남겨 FastMCP의 시그니처/inputSchema
        추론(inspect.signature)이 원 함수의 타입힌트를 그대로 보게 한다.
        """
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                return _SAFE_ERROR
        return wrapper

    @mcp.tool(
        name="quiz",
        description=(
            "Starts a stock quiz for 주식대결 (Stock Quiz Battle / 주식사전 퀴즈). "
            "Pick one of three modes: '주가' (guess a random stock's current price, "
            "±3% correct), '시장' (guess the biggest gainer or loser over a period; "
            "direction is random), '종목' (guess the company from sector/price/market-cap "
            "hints). The reply includes a short mode intro plus the quiz and a quiz_id; "
            "grade answers with submit_answer. Korean market only for now."
        ),
        annotations=ToolAnnotations(title="Stock Quiz", **_COMMON_ANN),
    )
    @_safe
    def quiz(
        mode: QuizMode,
        market: Market = Market.KR,
        period: Period = Period.TODAY,
        sector: Sector | None = None,
    ) -> str:
        return handlers.quiz(mode, market, period, sector).markdown

    @mcp.tool(
        name="submit_answer",
        description=(
            "Grades an answer for a 주식대결 (Stock Quiz Battle / 주식사전 퀴즈) quiz. "
            "Give the quiz_id and your answer (stock name or price number). "
            "Wrong answers return a staged hint; a correct answer returns a fact-only "
            "mini-analysis. Never gives buy/sell advice."
        ),
        annotations=ToolAnnotations(title="Submit Answer", **_COMMON_ANN),
    )
    async def submit_answer(quiz_id: str, answer: str) -> str:
        try:
            outcome = await handlers.submit_answer(quiz_id, answer)
            return outcome.markdown
        except Exception:
            return _SAFE_ERROR

    # ── 헬스체크: 낡은 데이터로 버티는 중임을 즉시 노출 ──────────
    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "stale": cache.stale,
                "data_as_of": (
                    cache.data_as_of.isoformat() if cache.data_as_of else None
                ),
            }
        )

    return mcp


async def _refresh_today(cache: QuizCache, client: MarketClient) -> None:
    """today 랭킹만 장중 갱신(gainers/losers × KR/US)."""
    for market in (Market.KR, Market.US):
        for direction in ("up", "down"):
            try:
                items = await client.top_movers(market, Period.TODAY, direction, 5)
                cache.update_movers(market, Period.TODAY, direction, items)
            except Exception:
                continue  # 리프레셔 실패는 기존 캐시 유지(서비스 지속)


async def _refresher_loop(cache: QuizCache, client: MarketClient) -> None:
    """장중 3회(10:00/13:00/15:40)만 실행. 5분 폴링 금지 — 다음 시각까지 sleep."""
    while True:
        now = _dt.datetime.now(_KST)
        today_times = [
            now.replace(hour=h, minute=m, second=0, microsecond=0)
            for h, m in _REFRESH_TIMES
        ]
        upcoming = [t for t in today_times if t > now]
        if upcoming:
            target = min(upcoming)
        else:
            target = min(today_times) + _dt.timedelta(days=1)
        await asyncio.sleep(max(1.0, (target - now).total_seconds()))
        await _refresh_today(cache, client)


def create_server() -> FastMCP:
    """실 구동용 조립: 캐시 로드(검증) + 실 KIS 클라이언트 리프레셔."""
    from clients.kis import KISClient

    cache = QuizCache(_DATA_DIR).load()  # 검증 실패 시 여기서 기동 중단
    store = QuizStore()
    client = KISClient()
    mcp = build_app(cache, store, refresh_client=client)

    @mcp.custom_route("/", methods=["GET"])
    async def root(request: Request) -> PlainTextResponse:
        return PlainTextResponse("stock-quiz-dictionary MCP")

    return mcp


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    run_kwargs: dict = {
        "transport": "streamable-http",
        "host": host,
        "port": port,
        "stateless_http": True,
        "json_response": True,  # SSE 프레이밍 오버헤드 제거(툴 호출 ~8ms 단축, 실측)
        # TLS 종료 프록시(Fly 등) 뒤에서 X-Forwarded-Proto를 신뢰 →
        # 슬래시 리다이렉트(/mcp/ → /mcp)가 http로 다운그레이드되는 문제 방지.
        "uvicorn_config": {"proxy_headers": True, "forwarded_allow_ips": "*"},
    }
    # 공개 배포 시 Host 헤더 보호 때문에 외부 도메인 요청이 막힐 수 있다.
    # ALLOWED_HOSTS="app.fly.dev,example.com" 로 허용 호스트 지정(권장),
    # 또는 DISABLE_HOST_PROTECTION=1 로 보호 해제(공개·읽기전용 서버라 허용 가능).
    allowed = os.environ.get("ALLOWED_HOSTS", "").strip()
    if allowed:
        run_kwargs["allowed_hosts"] = [h.strip() for h in allowed.split(",") if h.strip()]
    if os.environ.get("DISABLE_HOST_PROTECTION") == "1":
        run_kwargs["host_origin_protection"] = False
    create_server().run(**run_kwargs)
