"""닉네임 기반 주간 점수와 랭킹을 관리하는 인메모리 저장소."""

from __future__ import annotations

import asyncio
import json
from bisect import bisect_left, insort
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contracts.schemas import LeaderboardSnapshot, ScoreEntry

_KST = timezone(timedelta(hours=9))
_SCORE_TABLE = {1: 3, 2: 2}

DEFAULT_SNAPSHOT_PATH = Path(__file__).parent / "data" / "scores.json"
DEFAULT_RESET_WEEKDAY = 0   # 월요일 (datetime.weekday() 기준 0=월)
DEFAULT_RESET_HOUR = 0      # 00:00 KST — 실제 값은 팀 확인 필요, 임시 기본값


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

    def _remove_from_ranking(self, entry: ScoreEntry) -> None:
        key = self._ranking_key(entry)
        index = bisect_left(self._ranking, key)
        if index < len(self._ranking) and self._ranking[index] == key:
            self._ranking.pop(index)

    def _rebuild_ranking(self) -> None:
        self._ranking = sorted(self._ranking_key(entry) for entry in self._entries.values())

    async def add_result(self, identity_key: str, display_name: str, attempts: int) -> int:
        """정답 확정 시 호출. attempts(1부터)로 점수를 계산해 가산하고 이번에 획득한 점수를 반환한다.
        1회=3점, 2회=2점, 3회 이상=1점."""
        earned = _SCORE_TABLE.get(attempts, 1)
        now = self._now()
        async with self._lock:
            previous = self._entries.get(identity_key)
            if previous is not None:
                self._remove_from_ranking(previous)
                score = previous.score + earned
            else:
                score = earned
            entry = ScoreEntry(
                identity_key=identity_key,
                display_name=display_name,
                score=score,
                updated_at=now,
            )
            self._entries[identity_key] = entry
            insort(self._ranking, self._ranking_key(entry))
        return earned

    def leaderboard(self, identity_key: str, top_n: int = 5) -> LeaderboardSnapshot:
        """TOP N + 본인 정확한 순위. identity_key가 아직 없으면 score=0인 엔트리로 취급."""
        top = [self._entries[key].model_copy() for _, _, key in self._ranking[:top_n]]
        entry = self._entries.get(identity_key)
        if entry is None:
            my_entry = ScoreEntry(
                identity_key=identity_key,
                display_name=identity_key,
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

    async def snapshot_save(self) -> None:
        async with self._lock:
            payload = [
                self._entries[key].model_dump(mode="json")
                for _, _, key in self._ranking
            ]
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            self._snapshot_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def snapshot_load(self) -> None:
        if not self._snapshot_path.exists():
            return
        payload = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
        entries = [ScoreEntry.model_validate(item) for item in payload]
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
            return True
