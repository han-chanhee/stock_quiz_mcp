"""Local testbed helpers for widget, load, edge, and MCP conflict smoke checks.

This module is intentionally local-only. It is not imported by the public MCP
server and does not expose operational tools to end users.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from batch import DailyBatch, MockReasonProvider
from clients import MockMarketClient
from contracts.schemas import LeaderboardSnapshot, Market, Period, ScoreEntry
from server import widgets
from server.cache import QuizCache
from server.handlers import QuizHandlers, QuizMode
from services.quiz_bank import QuizBank
from store import QuizStore, ScoreStore
from store.quiz_store import DEFAULT_MAX_ENTRIES

_DATA_DIR = Path(__file__).resolve().parent.parent / "batch" / "data"
_EXPECTED_TOOLS = {"help", "quiz", "submit_answer"}
_FORBIDDEN_WIDGET_TYPES = {"Table"}


def sample_leaderboard() -> LeaderboardSnapshot:
    now = datetime.now(timezone.utc)
    entries = [
        ScoreEntry(
            identity_key=f"testbed-user-{rank}",
            display_name=f"테스트{rank}",
            score=30 - rank,
            updated_at=now,
        )
        for rank in range(1, 7)
    ]
    return LeaderboardSnapshot(
        top=entries[:5],
        my_entry=entries[-1],
        my_rank=6,
        week_started_at=now,
    )


def collect_widget_payloads() -> dict[str, dict]:
    leaderboard = sample_leaderboard()
    question_analysis = [
        "정답 종목명은 아직 공개하지 않습니다.",
        "등락 흐름과 가격대를 함께 봅니다.",
        "섹터 단서는 문제 난도를 낮춥니다.",
        "데이터 랭킹 단서는 보조 정보입니다.",
        "공개된 단서만 조합해 정답을 좁힙니다.",
    ]
    answer_analysis = [
        "삼성전자 현재가는 80,000원이고 등락률은 +1.20%입니다.",
        "출제 시점 기준 흐름은 상승으로 분류됩니다.",
        "섹터는 반도체, 가격대는 3만~10만원입니다.",
        "데이터 랭킹 단서는 1위권입니다.",
        "확인된 재료: 특별한 재료 확인 안 됨",
    ]
    payloads = {
        "welcome": widgets.welcome_widget(),
        "mode_selection": widgets.mode_selection_widget(),
        "price_quiz": widgets.price_quiz_widget(
            "QZ-TEST", "주가 퀴즈", "현재가는?", analysis_lines=question_analysis
        ),
        "market_quiz": widgets.market_quiz_widget(
            "QZ-MARKET",
            "시장 퀴즈",
            "가장 오른 종목은?",
            5.2,
            analysis_lines=question_analysis,
        ),
        "company_quiz": widgets.company_quiz_widget(
            "QZ-COMPANY", "종목 퀴즈", "이 회사는?", analysis_lines=question_analysis
        ),
        "chart_quiz": widgets.chart_quiz_widget(
            "QZ-CHART",
            "차트 퀴즈",
            "아래 차트 모양과 힌트로 종목명을 맞혀보세요.\n`▁▂▃▄▅▆▇`\n- 흐름: **상승**",
            analysis_lines=question_analysis,
        ),
        "wrong_answer": widgets.wrong_answer_widget("UP", 1),
        "correct_answer": widgets.correct_answer_widget(
            "삼성전자",
            "현재가 80,000원",
            "시가총액 1위",
            "특별한 재료 확인 안 됨",
            3,
            leaderboard,
            ["다음 퀴즈", "종료"],
            answer_analysis,
        ),
        "already_solved": widgets.already_solved_widget(),
        "expired_quiz": widgets.expired_quiz_widget(),
        "quiz_not_found": widgets.quiz_not_found_widget(),
        "us_blocked": widgets.us_blocked_widget(),
    }
    payloads["price_quiz_with_leaderboard"] = widgets.with_leaderboard(
        payloads["price_quiz"], leaderboard
    )
    return payloads


def validate_widget_payload(payload: dict) -> None:
    if set(payload) != {"widget", "copy_text", "name"}:
        raise ValueError(f"invalid payload keys: {sorted(payload)}")
    if payload["widget"].get("type") not in {"Card", "ListView"}:
        raise ValueError(f"invalid widget root: {payload['widget'].get('type')}")
    json.loads(json.dumps(payload, ensure_ascii=False))

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if "status" in value:
                raise ValueError(f"unsupported status key in {payload['name']}")
            if value.get("type") in _FORBIDDEN_WIDGET_TYPES:
                raise ValueError(f"unsupported widget type {value['type']} in {payload['name']}")
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)


def widget_report() -> dict[str, Any]:
    payloads = collect_widget_payloads()
    for payload in payloads.values():
        validate_widget_payload(payload)
    return {"count": len(payloads), "names": sorted(payloads)}


async def _load_cache(data_dir: Path = _DATA_DIR) -> QuizCache:
    if not data_dir.exists() or not any(data_dir.glob("*.json")):
        await DailyBatch(MockMarketClient(), data_dir=data_dir, reason_provider=MockReasonProvider()).run()
    return QuizCache(data_dir).load()


async def load_smoke(requests: int = 200, concurrency: int = 20) -> dict[str, Any]:
    cache = await _load_cache()
    store = QuizStore()
    score_store = ScoreStore()
    handlers = QuizHandlers(cache, store, score_store, QuizBank(rng=random.Random(0)), rng=random.Random(0))
    sem = asyncio.Semaphore(concurrency)

    async def one(index: int) -> None:
        async with sem:
            nickname = f"부하{index % 25}"
            outcome = handlers.quiz(QuizMode.PRICE, nickname, Market.KR, Period.TODAY)
            state = store.get(outcome.quiz_id)
            await handlers.submit_answer(outcome.quiz_id, str(state.answer.price * 0.5), nickname)

    start = time.perf_counter()
    await asyncio.gather(*(one(index) for index in range(requests)))
    elapsed = time.perf_counter() - start
    return {
        "requests": requests,
        "concurrency": concurrency,
        "elapsed_sec": round(elapsed, 4),
        "rps": round(requests / elapsed, 2) if elapsed else requests,
        "stored_quizzes": len(store),
        "max_active_quizzes": DEFAULT_MAX_ENTRIES,
        "cap_reached": len(store) == DEFAULT_MAX_ENTRIES,
    }


async def conflict_report() -> dict[str, Any]:
    from server.main import build_app

    cache = await _load_cache()
    app = build_app(cache, QuizStore(), ScoreStore(), QuizBank(rng=random.Random(0)))
    names = sorted(tool.name for tool in await app.list_tools())
    duplicates = sorted(name for name in set(names) if names.count(name) > 1)
    missing = sorted(_EXPECTED_TOOLS - set(names))
    extra = sorted(set(names) - _EXPECTED_TOOLS)
    return {
        "names": names,
        "duplicates": duplicates,
        "missing": missing,
        "extra": extra,
        "ok": not duplicates and not missing and not extra,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Stock Quiz MCP testbed")
    sub = parser.add_subparsers(dest="command", required=True)

    widgets_cmd = sub.add_parser("widgets", help="validate representative widget payloads")
    widgets_cmd.set_defaults(func=lambda args: widget_report())

    load_cmd = sub.add_parser("load", help="run in-process quiz/answer load smoke")
    load_cmd.add_argument("--requests", type=int, default=200)
    load_cmd.add_argument("--concurrency", type=int, default=20)
    load_cmd.set_defaults(
        func=lambda args: asyncio.run(load_smoke(args.requests, args.concurrency))
    )

    conflicts = sub.add_parser("conflicts", help="check MCP tool-name conflicts")
    conflicts.set_defaults(func=lambda args: asyncio.run(conflict_report()))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report = args.func(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if isinstance(report, dict) and report.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
