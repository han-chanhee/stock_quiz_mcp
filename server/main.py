"""모듈 E: FastMCP 엔트리. 툴 5개 등록 + 의존성 주입 지점 build_app 하나.

Streamable HTTP, stateless_http=True.
※ MCP 프로토콜 세션은 미사용(stateless). 퀴즈 상태(quiz_id)는 QuizStore가 별도로
   보관한다 — 프로토콜 세션과 앱 상태는 별개다.

의존성 주입은 build_app(cache, store, score_store, bank, refresh_client) 한 곳에서 끝난다.
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
from fastmcp import Context
from fastmcp.tools.tool import ToolAnnotations
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse

from contracts.schemas import Market, Period, Sector

from clients.base import MarketClient
from services.quiz_bank import QuizBank
from store import QuizStore, ScoreStore

from .cache import QuizCache
from .auth import build_auth_provider, register_auth_routes
from .handlers import QuizHandlers, QuizMode
from . import widgets

_KST = timezone(_dt.timedelta(hours=9))
_DATA_DIR = Path(__file__).resolve().parent.parent / "batch" / "data"

# 장중에 한해 1분 간격으로 시세 캐시를 갱신한다.
_REFRESH_INTERVAL_SEC: int = 60
_WEEKLY_RESET_INTERVAL_SEC: int = 60
_SNAPSHOT_INTERVAL_SEC: int = 300
_MARKET_OPEN = (9, 0)  # KST
_MARKET_CLOSE = (15, 30)  # KST

_COMMON_ANN = dict(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
    idempotentHint=False,
)

_SAFE_ERROR = "잠시 후 다시 시도해주세요."


def _public_https_url(request: Request, path: str) -> str:
    host = request.headers.get("host") or request.url.netloc
    return f"https://{host}{path}"


class ForwardedHttpsRedirectMiddleware(BaseHTTPMiddleware):
    """TLS 종료 프록시 뒤에서 Starlette 슬래시 리다이렉트가 http로 내려가지 않게 보정."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        location = response.headers.get("location", "")
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        is_forwarded_https = forwarded_proto.split(",", 1)[0].strip().lower() == "https"
        if is_forwarded_https and location.startswith("http://"):
            response.headers["location"] = "https://" + location.removeprefix("http://")
        return response


def _tool_identity_key(nickname: str | None, ctx: Context | None) -> str | None:
    """툴 호출 컨텍스트에서 점수용 식별자를 뽑는다.

    OAuth client_id는 PlayMCP 앱 식별자이지 최종 사용자가 아니므로 점수 키로 쓰지
    않는다. 플랫폼이 subject/user_id 메타를 제공하면 그 값만 사용하고, 없으면
    핸들러가 닉네임 fallback을 쓴다.
    """
    if ctx is None:
        return None
    meta = getattr(ctx.request_context, "meta", None) if ctx.request_context else None
    for name in ("subject", "user_id", "client_id"):
        value = getattr(meta, name, None) if meta is not None else None
        if value is None and isinstance(meta, dict):
            value = meta.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def build_app(
    cache: QuizCache,
    store: QuizStore,
    score_store: ScoreStore,
    bank: QuizBank | None = None,
    refresh_client: MarketClient | None = None,
) -> FastMCP:
    """단일 의존성 주입 지점. 퀴즈·점수 저장소와 선택 의존성을 꽂는다."""
    return _build_app(cache, store, score_store, bank, refresh_client)


def _build_app(
    cache: QuizCache,
    store: QuizStore,
    score_store: ScoreStore,
    bank: QuizBank | None = None,
    refresh_client: MarketClient | None = None,
    *,
    auth=None,
) -> FastMCP:
    """실제 FastMCP 조립. 인증은 실 구동 경로에서만 조건부로 주입한다."""
    handlers = QuizHandlers(cache, store, score_store, bank)

    @contextlib.asynccontextmanager
    async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
        # 리프레셔 클라이언트가 주입된 경우에만 장중 1분 갱신 태스크를 띄운다.
        tasks = [
            asyncio.create_task(_weekly_reset_loop(score_store)),
            asyncio.create_task(_snapshot_loop(score_store)),
        ]
        if refresh_client is not None:
            tasks.append(asyncio.create_task(_refresher_loop(cache, refresh_client)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    # stateless_http는 run()/http_app() 시점에 전달한다(FastMCP 3.x API).
    # MCP 프로토콜 세션 미사용 — quiz 상태는 QuizStore가 별도로 보관한다.
    if auth is None:
        mcp = FastMCP(name="stock-quiz-dictionary", lifespan=_lifespan)
    else:
        mcp = FastMCP(name="stock-quiz-dictionary", lifespan=_lifespan, auth=auth)

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
        name="help",
        description=(
            "Shows how to play 주식대결 (Stock Quiz Battle / 주식사전 퀴즈): the three "
            "quiz modes (주가/시장/종목), why a nickname is needed for the weekly "
            "ranking, and example phrases to start. Call this when the user asks how "
            "the quiz works, what modes exist, or seems unsure how to start."
        ),
        annotations=ToolAnnotations(title="How to Play", **_COMMON_ANN),
    )
    @_safe
    def help() -> str:
        return widgets.to_content_text(widgets.welcome_widget())

    @mcp.tool(
        name="quiz",
        description=(
            "Starts a stock quiz for 주식대결 (Stock Quiz Battle / 주식사전 퀴즈). "
            "Requires mode and nickname — if either is missing, call this tool anyway "
            "with what you have; it replies with a short guide instead of erroring. "
            "Pick one of three modes: '주가' (guess a random stock's current price, "
            "±3% correct), '시장' (guess the biggest gainer or loser over a period; "
            "direction is random), '종목' (guess the company from sector/price/market-cap "
            "hints). The reply includes a short mode intro plus the quiz and a quiz_id; "
            "grade answers with submit_answer. nickname (닉네임) is required for scoring. "
            "Korean market only for now."
        ),
        annotations=ToolAnnotations(title="Stock Quiz", **_COMMON_ANN),
    )
    @_safe
    def quiz(
        mode: QuizMode | None = None,
        nickname: str | None = None,
        market: Market = Market.KR,
        period: Period = Period.TODAY,
        sector: Sector | None = None,
        ctx: Context | None = None,
    ) -> str:
        outcome = handlers.quiz(
            mode,
            nickname,
            market,
            period,
            sector,
            identity_key=_tool_identity_key(nickname, ctx),
        )
        if outcome.widget is not None:
            return widgets.to_content_text(outcome.widget)
        return outcome.markdown

    @mcp.tool(
        name="submit_answer",
        description=(
            "Grades an answer for a 주식대결 (Stock Quiz Battle / 주식사전 퀴즈) quiz. "
            "Give the quiz_id and your answer (stock name or price number). "
            "nickname (닉네임) is required for scoring and ranking. "
            "Wrong answers return a staged hint; a correct answer returns a fact-only "
            "mini-analysis. Never gives buy/sell advice."
        ),
        annotations=ToolAnnotations(title="Submit Answer", **_COMMON_ANN),
    )
    async def submit_answer(
        quiz_id: str,
        answer: str,
        nickname: str,
        ctx: Context | None = None,
    ) -> str:
        try:
            outcome = await handlers.submit_answer(
                quiz_id,
                answer,
                nickname,
                identity_key=_tool_identity_key(nickname, ctx),
            )
            if outcome.widget is not None:
                return widgets.to_content_text(outcome.widget)
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

    @mcp.custom_route("/mcp/", methods=["POST", "DELETE"], include_in_schema=False)
    async def mcp_trailing_slash(request: Request):
        return RedirectResponse(_public_https_url(request, "/mcp"), status_code=307)

    return mcp


async def _refresh_today(cache: QuizCache, client: MarketClient) -> None:
    """today 랭킹과 시총 상위 종목을 장중 갱신한다."""
    for market in (Market.KR, Market.US):
        for direction in ("up", "down"):
            try:
                items = await client.top_movers(market, Period.TODAY, direction, 5)
                cache.update_movers(market, Period.TODAY, direction, items)
            except Exception:
                continue  # 리프레셔 실패는 기존 캐시 유지(서비스 지속)

        try:
            snaps = await client.top_market_cap(market, 20)
            cache.update_top20(market, snaps)
        except Exception:
            continue  # 시장별 부분 실패는 다른 시장의 갱신을 막지 않는다.


def _now_kst() -> _dt.datetime:
    """테스트에서 교체 가능한 KST 시계."""
    return _dt.datetime.now(_KST)


def _is_market_open(now: _dt.datetime) -> bool:
    current = (now.hour, now.minute)
    return _MARKET_OPEN <= current <= _MARKET_CLOSE


async def _refresher_loop(cache: QuizCache, client: MarketClient) -> None:
    """장중(09:00~15:30 KST)에만 1분 간격으로 캐시를 갱신한다."""
    while True:
        if _is_market_open(_now_kst()):
            # 항목별 실패가 격리되어 top20/movers의 신선도가 다를 수 있다.
            # data_as_of는 전체의 최신값만 추적하며 필드별 stale 판정은 범위 밖이다.
            await _refresh_today(cache, client)
        await asyncio.sleep(_REFRESH_INTERVAL_SEC)


async def _weekly_reset_loop(score_store: ScoreStore) -> None:
    """주간 리셋 시점을 1분 간격으로 확인한다."""
    while True:
        await score_store.maybe_weekly_reset(_now_kst())
        await asyncio.sleep(_WEEKLY_RESET_INTERVAL_SEC)


async def _snapshot_loop(score_store: ScoreStore) -> None:
    """랭킹 스냅샷을 5분 간격으로 저장한다."""
    while True:
        await asyncio.sleep(_SNAPSHOT_INTERVAL_SEC)
        await score_store.snapshot_save()


def create_server() -> FastMCP:
    """실 구동용 조립: 캐시 로드(검증) + 실 KIS 클라이언트 리프레셔."""
    from clients.kis import KISClient

    cache = QuizCache(_DATA_DIR).load()  # 검증 실패 시 여기서 기동 중단
    store = QuizStore()
    score_store = ScoreStore()
    score_store.snapshot_load()
    client = KISClient()
    auth = build_auth_provider()

    # OAuth 활성화 시 auth를 주입한다. 이때 인증 없는 요청은 401을 받게 되는데,
    # 이는 MCP 인증 스펙(2025-03-26 Authorization)이 명시적으로 요구하는 동작이다:
    #   "When authorization is required and not yet proven by the client, servers
    #    MUST respond with HTTP 401 Unauthorized. Clients initiate the OAuth 2.1
    #    authorization flow after receiving the HTTP 401 Unauthorized."
    # 즉 401은 장애가 아니라 클라이언트에게 인증 흐름 시작을 알리는 신호이며,
    # 이 신호가 있어야 플랫폼이 사용자에게 동의 화면을 띄운다.
    mcp = _build_app(
        cache,
        store,
        score_store,
        refresh_client=client,
        auth=auth,
    )

    # OAuth 활성화 시(OAUTH_ENABLED=1)만 동의/연동해제 화면을 등록한다.
    if auth is not None:
        register_auth_routes(mcp, auth)

    @mcp.custom_route("/", methods=["GET"])
    async def root(request: Request) -> PlainTextResponse:
        return PlainTextResponse("stock-quiz-dictionary MCP")

    return mcp


def _runtime_middleware() -> list[Middleware]:
    return [Middleware(ForwardedHttpsRedirectMiddleware)]


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
        "middleware": _runtime_middleware(),
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
