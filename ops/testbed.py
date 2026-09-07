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
from contracts.schemas import LeaderboardSnapshot, Market, Period, ScoreEntry, Sector
from server import widgets
from server.cache import QuizCache
from server.handlers import QuizHandlers, QuizMode
from services import PRICE_UNIT_KRW, judge_price, pick_hint
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
    payloads = {
        "welcome": widgets.welcome_widget(),
        "mode_selection": widgets.mode_selection_widget(),
        "price_quiz": widgets.price_quiz_widget("QZ-TEST", "주가 퀴즈", "현재가는?"),
        "market_quiz": widgets.market_quiz_widget("QZ-MARKET", "시장 퀴즈", "가장 오른 종목은?", 5.2),
        "company_quiz": widgets.company_quiz_widget("QZ-COMPANY", "종목 퀴즈", "이 회사는?"),
        "wrong_answer": widgets.wrong_answer_widget("UP", 1),
        "correct_answer": widgets.correct_answer_widget(
            "삼성전자",
            "현재가 80,000원",
            "시가총액 1위",
            "특별한 재료 확인 안 됨",
            3,
            leaderboard,
            ["다음 퀴즈", "종료"],
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


async def qa_report(runs: int = 300) -> dict[str, Any]:
    """Run repeated local QA over real cached pools and core game flows."""
    if runs < 300:
        raise ValueError("QA runs must be at least 300")

    cache = await _load_cache()
    store = QuizStore()
    score_store = ScoreStore()
    bank = QuizBank(rng=random.Random(20260907))
    handlers = QuizHandlers(
        cache,
        store,
        score_store,
        bank,
        rng=random.Random(20260907),
    )
    sector_answers: dict[str, int] = {}
    price_answers: dict[str, int] = {}
    price_checked = 0
    sector_checked = 0

    for index in range(runs):
        price_out = handlers.price_quiz(Market.KR)
        price_state = store.get(price_out.quiz_id)
        if price_state is None:
            raise AssertionError("price quiz was not stored")
        price_checked += 1
        answer = price_state.answer
        price_answers[answer.name] = price_answers.get(answer.name, 0) + 1
        bucket = int((answer.price + (PRICE_UNIT_KRW / 2)) // PRICE_UNIT_KRW)
        if not judge_price(answer, str(bucket)):
            raise AssertionError(f"correct KR bucket rejected: {answer.name} {answer.price}")
        if bucket > 1 and pick_hint(price_state, str(bucket - 1), 1).text != "UP":
            raise AssertionError(f"lower KR bucket did not produce UP: {answer.name}")
        if pick_hint(price_state, str(bucket + 1), 1).text != "DOWN":
            raise AssertionError(f"higher KR bucket did not produce DOWN: {answer.name}")

        sector = Sector.INTERNET_GAME if index % 2 == 0 else None
        sector_out = handlers.guess_company(sector, Market.KR)
        sector_state = store.get(sector_out.quiz_id)
        if sector_state is None:
            raise AssertionError("company quiz was not stored")
        sector_checked += 1
        if sector is not None and sector_state.answer.sector != sector:
            raise AssertionError(
                f"sector quiz ignored filter: expected {sector}, got {sector_state.answer.sector}"
            )
        sector_answers[sector_state.answer.name] = (
            sector_answers.get(sector_state.answer.name, 0) + 1
        )

    internet_names = {
        item.name for item in cache.sector_pool(Sector.INTERNET_GAME)
    }
    internet_answered = internet_names & set(sector_answers)
    if len(internet_names) > 1 and len(internet_answered) < 2:
        raise AssertionError("internet/game sector did not vary across QA runs")

    return {
        "ok": True,
        "runs": runs,
        "price_checked": price_checked,
        "price_unique_answers": len(price_answers),
        "sector_checked": sector_checked,
        "sector_unique_answers": len(sector_answers),
        "internet_game_pool": sorted(internet_names),
        "internet_game_answered": sorted(internet_answered),
        "stored_quizzes": len(store),
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

    qa = sub.add_parser("qa", help="run repeated local QA over game behavior")
    qa.add_argument("--runs", type=int, default=300)
    qa.set_defaults(func=lambda args: asyncio.run(qa_report(args.runs)))
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
