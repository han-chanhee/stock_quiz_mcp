"""로컬 테스트베드 CLI/헬퍼 검증."""

from __future__ import annotations

import pytest

from ops import testbed


def test_widget_testbed_validates_representative_payloads() -> None:
    report = testbed.widget_report()

    assert report["count"] >= 10
    assert "market_quiz" in report["names"]
    assert "price_quiz_with_leaderboard" in report["names"]


def test_widget_testbed_rejects_preview_unsafe_components() -> None:
    payload = {
        "widget": {"type": "Card", "children": [{"type": "Table"}]},
        "copy_text": "unsafe",
        "name": "unsafe",
    }

    with pytest.raises(ValueError, match="unsupported widget type"):
        testbed.validate_widget_payload(payload)


@pytest.mark.asyncio
async def test_conflict_testbed_reports_expected_tool_names() -> None:
    report = await testbed.conflict_report()

    assert report["ok"] is True
    assert report["names"] == ["help", "quiz", "submit_answer"]


@pytest.mark.asyncio
async def test_load_testbed_runs_in_process_smoke() -> None:
    report = await testbed.load_smoke(requests=12, concurrency=4)

    assert report["requests"] == 12
    assert report["stored_quizzes"] == 12
    assert report["rps"] > 0
