"""OAuth/플랫폼 식별자 또는 닉네임 기반 주간 점수와 랭킹을 관리하는 저장소."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from bisect import bisect_left, insort
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contracts.schemas import LeaderboardSnapshot, ScoreEntry

_KST = timezone(timedelta(hours=9))
_CORRECT_SCORE_TABLE = {1: 3, 2: 2}
WRONG_PENALTY = -1

DEFAULT_SNAPSHOT_PATH = Path(__file__).parent / "data" / "scores.json"
DEFAULT_RESET_WEEKDAY = 0   # 월요일 (datetime.weekday() 기준 0=월)
DEFAULT_RESET_HOUR = 0      # 00:00 KST — 실제 값은 팀 확인 필요, 임시 기본값
MAX_DISPLAY_NAME_LEN = 24
_MARKDOWN_UNSAFE = str.maketrans({
    "`": "'",
    "*": "",
    "_": " ",
    "[": "(",
    "]": ")",
    "<": "(",
    ">": ")",
})
_AUTO_NICKNAME_PREFIX = "주식러"


class ScoreStore:
    """점수 엔트리와 점수순 인덱스를 함께 유지한다."""

    def __init__(
        self,
        snapshot_path: Path | None = None,
        reset_weekday: int = DEFAULT_RESET_WEEKDAY,
        reset_hour: int = DEFAULT_RESET_HOUR,
    ) -> None:
        self._snapshot_path = snapshot_path or DEFAULT_SNAPSHOT_PATH
        self._reset_weekday = reset_weekday
        self._reset_hour = reset_hour
        self._entries: dict[str, ScoreEntry] = {}
        self._ranking: list[tuple[int, datetime, str]] = []
        self._lock = asyncio.Lock()
        self._last_reset_at: datetime | None = None
        self._week_started_at = self._reset_boundary(self._now())
        self._quiz_starts = 0
        self._submitted_answers = 0
        self._correct_answers = 0
        self._wrong_answers = 0
        self._quiz_players: set[str] = set()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(_KST)

    def _reset_boundary(self, now: datetime) -> datetime:
        """now 이전(포함)의 가장 가까운 주간 리셋 시각을 계산한다."""
        days_since_reset = (now.weekday() - self._reset_weekday) % 7
        boundary = (now - timedelta(days=days_since_reset)).replace(
            hour=self._reset_hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        if boundary > now:
            boundary -= timedelta(days=7)
        return boundary

    @staticmethod
    def _ranking_key(entry: ScoreEntry) -> tuple[int, datetime, str]:
        return (-entry.score, entry.updated_at, entry.identity_key)

    @staticmethod
    def _clean_display_name(value: str | None) -> str:
        text = " ".join((value or "").split()).translate(_MARKDOWN_UNSAFE).strip()
        if not text:
            return "익명 참가자"
        if len(text) > MAX_DISPLAY_NAME_LEN:
            return text[:MAX_DISPLAY_NAME_LEN - 1] + "…"
        return text

    def generated_display_name(self, identity_key: str) -> str:
        """OAuth identity별로 안정적인 자동 닉네임을 만든다."""
        digest = hashlib.sha256(identity_key.encode("utf-8")).hexdigest()
        suffix = int(digest[:8], 16) % 10000
        return f"{_AUTO_NICKNAME_PREFIX}{suffix:04d}"

    def display_name_for(self, identity_key: str) -> str | None:
        entry = self._entries.get(identity_key)
        return entry.display_name if entry is not None else None

    def _quarantine_snapshot(self) -> None:
        if not self._snapshot_path.exists():
            return
        target = self._snapshot_path.with_suffix(
            self._snapshot_path.suffix + f".corrupt.{int(time.time())}"
        )
        self._snapshot_path.replace(target)

    def _remove_from_ranking(self, entry: ScoreEntry) -> None:
        key = self._ranking_key(entry)
        index = bisect_left(self._ranking, key)
        if index < len(self._ranking) and self._ranking[index] == key:
            self._ranking.pop(index)

    def _rebuild_ranking(self) -> None:
        self._ranking = sorted(self._ranking_key(entry) for entry in self._entries.values())

    async def _apply_delta(
        self,
        identity_key: str,
        display_name: str,
        delta: int,
        verdict: str,
    ) -> int:
        now = self._now()
        safe_display_name = self._clean_display_name(display_name)
        async with self._lock:
            previous = self._entries.get(identity_key)
            if previous is not None:
                self._remove_from_ranking(previous)
                score = previous.score + delta
            else:
                score = delta
            entry = ScoreEntry(
                identity_key=identity_key,
                display_name=safe_display_name,
                score=score,
                updated_at=now,
            )
            self._entries[identity_key] = entry
            insort(self._ranking, self._ranking_key(entry))
            self._submitted_answers += 1
            if verdict == "correct":
                self._correct_answers += 1
            elif verdict == "wrong":
                self._wrong_answers += 1
        return delta

    async def add_result(self, identity_key: str, display_name: str, attempts: int) -> int:
        """정답 확정 시 호출. attempts(1부터)로 점수를 계산해 가산하고 이번에 획득한 점수를 반환한다.
        1회=3점, 2회=2점, 3회 이상=1점."""
        earned = _CORRECT_SCORE_TABLE.get(attempts, 1)
        return await self._apply_delta(identity_key, display_name, earned, "correct")

    async def add_penalty(self, identity_key: str, display_name: str) -> int:
        """오답 확정 시 호출. 이번 감점 값을 반환한다."""
        return await self._apply_delta(identity_key, display_name, WRONG_PENALTY, "wrong")

    def record_quiz_started(self, identity_key: str | None) -> None:
        if identity_key is None or not identity_key.strip():
            return
        self._quiz_starts += 1
        self._quiz_players.add(identity_key.strip())

    def leaderboard(
        self,
        identity_key: str,
        top_n: int = 3,
        display_name: str | None = None,
    ) -> LeaderboardSnapshot:
        """TOP N + 본인 정확한 순위. identity_key가 아직 없으면 score=0인 엔트리로 취급."""
        top = [self._entries[key].model_copy() for _, _, key in self._ranking[:top_n]]
        entry = self._entries.get(identity_key)
        if entry is None:
            my_entry = ScoreEntry(
                identity_key=identity_key,
                display_name=self._clean_display_name(display_name or identity_key),
                score=0,
                updated_at=self._now(),
            )
        else:
            my_entry = entry.model_copy()
        return LeaderboardSnapshot(
            top=top,
            my_entry=my_entry,
            my_rank=self.rank_of(identity_key),
            week_started_at=self._week_started_at,
        )

    def rank_of(self, identity_key: str) -> int:
        """1-based 순위. 미기록 유저는 전체 인원+1위로 취급(최하위)."""
        entry = self._entries.get(identity_key)
        if entry is None:
            return len(self._ranking) + 1
        return bisect_left(self._ranking, self._ranking_key(entry)) + 1

    def stats(self, top_n: int = 3) -> dict[str, object]:
        top = [
            {
                "rank": rank,
                "display_name": entry.display_name,
                "score": entry.score,
            }
            for rank, entry in enumerate(
                (self._entries[key] for _, _, key in self._ranking[:top_n]),
                start=1,
            )
        ]
        return {
            "participants": len(self._entries),
            "quiz_players": len(self._quiz_players),
            "quiz_starts": self._quiz_starts,
            "submitted_answers": self._submitted_answers,
            "correct_answers": self._correct_answers,
            "wrong_answers": self._wrong_answers,
            "top": top,
            "week_started_at": self._week_started_at.isoformat(),
        }

    async def snapshot_save(self) -> None:
        async with self._lock:
            payload = {
                "version": 1,
                "week_started_at": self._week_started_at.isoformat(),
                "last_reset_at": (
                    self._last_reset_at.isoformat()
                    if self._last_reset_at is not None
                    else None
                ),
                "entries": [
                    self._entries[key].model_dump(mode="json")
                    for _, _, key in self._ranking
                ],
                "metrics": {
                    "quiz_starts": self._quiz_starts,
                    "submitted_answers": self._submitted_answers,
                    "correct_answers": self._correct_answers,
                    "wrong_answers": self._wrong_answers,
                    "quiz_players": sorted(self._quiz_players),
                },
            }
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._snapshot_path.with_suffix(self._snapshot_path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._snapshot_path)

    def snapshot_load(self) -> None:
        if not self._snapshot_path.exists():
            return
        try:
            payload = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                entries_payload = payload
            else:
                entries_payload = payload.get("entries", [])
                if payload.get("week_started_at"):
                    self._week_started_at = datetime.fromisoformat(payload["week_started_at"])
                if payload.get("last_reset_at"):
                    self._last_reset_at = datetime.fromisoformat(payload["last_reset_at"])
                metrics = payload.get("metrics", {})
                if isinstance(metrics, dict):
                    self._quiz_starts = int(metrics.get("quiz_starts", 0))
                    self._submitted_answers = int(metrics.get("submitted_answers", 0))
                    self._correct_answers = int(metrics.get("correct_answers", 0))
                    self._wrong_answers = int(metrics.get("wrong_answers", 0))
                    raw_players = metrics.get("quiz_players", [])
                    if isinstance(raw_players, list):
                        self._quiz_players = {
                            str(item) for item in raw_players if str(item).strip()
                        }
            entries = []
            for item in entries_payload:
                entry = ScoreEntry.model_validate(item)
                entries.append(
                    entry.model_copy(
                        update={"display_name": self._clean_display_name(entry.display_name)}
                    )
                )
        except (json.JSONDecodeError, ValueError, TypeError):
            self._quarantine_snapshot()
            return
        self._entries = {entry.identity_key: entry for entry in entries}
        self._rebuild_ranking()

    async def maybe_weekly_reset(self, now: datetime) -> bool:
        """now가 리셋 시점을 지났고 아직 이번 주기에 리셋 안 했으면 전원 초기화. 리셋 여부를 반환."""
        boundary = self._reset_boundary(now)
        async with self._lock:
            if self._last_reset_at is not None and self._last_reset_at >= boundary:
                return False
            for identity_key, previous in list(self._entries.items()):
                self._entries[identity_key] = previous.model_copy(
                    update={"score": 0, "updated_at": boundary}
                )
            self._rebuild_ranking()
            self._last_reset_at = boundary
            self._week_started_at = boundary
            self._quiz_starts = 0
            self._submitted_answers = 0
            self._correct_answers = 0
            self._wrong_answers = 0
            self._quiz_players.clear()
            return True
