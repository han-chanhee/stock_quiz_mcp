"""SQLite 기반 런타임 저장소.

메모리 저장소와 같은 인터페이스를 유지하면서 운영 경로에서 퀴즈/랭킹 상태를
파일 DB로 분리한다. 단일 컨테이너 기준 재시작 내구성과 더 큰 활성 퀴즈 풀을
얻기 위한 구현이며, 여러 컨테이너가 서로 다른 디스크를 쓰는 문제까지 해결하지는
않는다.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contracts.schemas import LeaderboardSnapshot, QuizState, ScoreEntry, Verdict

from .quiz_store import DEFAULT_TTL_SEC, QuizStore
from .score_store import ScoreStore, WRONG_PENALTY

_KST = timezone(timedelta(hours=9))
DEFAULT_SQLITE_PATH = Path(__file__).parent / "data" / "runtime.sqlite3"
DEFAULT_SQLITE_MAX_QUIZZES = 1_000_000


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=_KST)
    return value.timestamp()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


class SQLiteQuizStore(QuizStore):
    """TTL + SQLite 저장 퀴즈 상태소."""

    def __init__(
        self,
        db_path: Path | None = None,
        ttl_sec: int = DEFAULT_TTL_SEC,
        max_entries: int = DEFAULT_SQLITE_MAX_QUIZZES,
    ) -> None:
        self._ttl = timedelta(seconds=ttl_sec)
        self._max = max_entries
        self._conn = _connect(db_path or DEFAULT_SQLITE_PATH)
        self._lock = asyncio.Lock()
        self._init_schema()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(_KST)

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quiz_states (
                quiz_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_quiz_created_at ON quiz_states(created_at)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_quiz_updated_at ON quiz_states(updated_at)"
        )

    def _expired(self, state: QuizState, now: datetime | None = None) -> bool:
        now = now or self._now()
        created_at = state.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=_KST)
        return now - created_at > self._ttl

    def _row_state(self, row: sqlite3.Row | None) -> QuizState | None:
        if row is None:
            return None
        return QuizState.model_validate_json(row["payload"])

    def _delete(self, quiz_id: str) -> None:
        self._conn.execute("DELETE FROM quiz_states WHERE quiz_id = ?", (quiz_id,))

    def _evict_if_needed(self) -> None:
        overflow = len(self) - self._max
        if overflow <= 0:
            return
        self._conn.execute(
            """
            DELETE FROM quiz_states
            WHERE quiz_id IN (
                SELECT quiz_id FROM quiz_states
                ORDER BY updated_at ASC
                LIMIT ?
            )
            """,
            (overflow,),
        )

    def put(self, state: QuizState) -> None:
        now = self._now().timestamp()
        self._conn.execute(
            """
            INSERT INTO quiz_states(quiz_id, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(quiz_id) DO UPDATE SET
                payload = excluded.payload,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (state.quiz_id, state.model_dump_json(), _timestamp(state.created_at), now),
        )
        self._evict_if_needed()

    def get(self, quiz_id: str) -> QuizState | None:
        row = self._conn.execute(
            "SELECT payload FROM quiz_states WHERE quiz_id = ?", (quiz_id,)
        ).fetchone()
        state = self._row_state(row)
        if state is None:
            return None
        if self._expired(state):
            self._delete(quiz_id)
            return None
        self._conn.execute(
            "UPDATE quiz_states SET updated_at = ? WHERE quiz_id = ?",
            (self._now().timestamp(), quiz_id),
        )
        return state

    def get_state_or_verdict(
        self, quiz_id: str
    ) -> tuple[QuizState | None, Verdict | None]:
        row = self._conn.execute(
            "SELECT payload FROM quiz_states WHERE quiz_id = ?", (quiz_id,)
        ).fetchone()
        state = self._row_state(row)
        if state is None:
            return None, Verdict.NOT_FOUND
        if self._expired(state):
            self._delete(quiz_id)
            return None, Verdict.EXPIRED
        self._conn.execute(
            "UPDATE quiz_states SET updated_at = ? WHERE quiz_id = ?",
            (self._now().timestamp(), quiz_id),
        )
        return state, None

    def update(self, state: QuizState) -> None:
        self._conn.execute(
            "UPDATE quiz_states SET payload = ?, updated_at = ? WHERE quiz_id = ?",
            (state.model_dump_json(), self._now().timestamp(), state.quiz_id),
        )

    def purge_expired(self) -> int:
        threshold = (self._now() - self._ttl).timestamp()
        before = len(self)
        self._conn.execute("DELETE FROM quiz_states WHERE created_at < ?", (threshold,))
        return before - len(self)

    async def compare_and_solve(self, quiz_id: str) -> tuple[QuizState | None, bool]:
        async with self._lock:
            state = self.get(quiz_id)
            if state is None:
                return None, False
            if state.solved:
                return state, False
            state.solved = True
            self.update(state)
            return state, True

    async def record_attempt(self, quiz_id: str) -> QuizState | None:
        async with self._lock:
            state = self.get(quiz_id)
            if state is None:
                return None
            state.attempts += 1
            self.update(state)
            return state

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS count FROM quiz_states").fetchone()
        return int(row["count"])


class SQLiteScoreStore(ScoreStore):
    """SQLite 기반 주간 점수/랭킹 저장소."""

    def __init__(
        self,
        db_path: Path | None = None,
        snapshot_path: Path | None = None,
        reset_weekday: int = 0,
        reset_hour: int = 0,
    ) -> None:
        super().__init__(
            snapshot_path=snapshot_path,
            reset_weekday=reset_weekday,
            reset_hour=reset_hour,
        )
        self._conn = _connect(db_path or DEFAULT_SQLITE_PATH)
        self._lock = asyncio.Lock()
        self._init_schema()
        stored_week = self._meta("week_started_at")
        if stored_week:
            self._week_started_at = datetime.fromisoformat(stored_week)
        else:
            self._set_meta("week_started_at", self._week_started_at.isoformat())

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scores (
                identity_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                score INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scores_rank ON scores(score DESC, updated_at ASC, identity_key ASC)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS metrics (key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS quiz_players (identity_key TEXT PRIMARY KEY)"
        )

    def _meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else None

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _metric(self, key: str) -> int:
        row = self._conn.execute(
            "SELECT value FROM metrics WHERE key = ?", (key,)
        ).fetchone()
        return int(row["value"]) if row is not None else 0

    def _increment_metric(self, key: str, amount: int = 1) -> None:
        self._conn.execute(
            """
            INSERT INTO metrics(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = value + excluded.value
            """,
            (key, amount),
        )

    def _score_entry_from_row(self, row: sqlite3.Row) -> ScoreEntry:
        return ScoreEntry(
            identity_key=row["identity_key"],
            display_name=row["display_name"],
            score=int(row["score"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def display_name_for(self, identity_key: str) -> str | None:
        row = self._conn.execute(
            "SELECT display_name FROM scores WHERE identity_key = ?", (identity_key,)
        ).fetchone()
        return str(row["display_name"]) if row is not None else None

    async def _apply_delta(
        self,
        identity_key: str,
        display_name: str,
        delta: int,
        verdict: str,
    ) -> int:
        now = self._now().isoformat()
        safe_display_name = self._clean_display_name(display_name)
        async with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT score FROM scores WHERE identity_key = ?",
                    (identity_key,),
                ).fetchone()
                score = (int(row["score"]) if row is not None else 0) + delta
                self._conn.execute(
                    """
                    INSERT INTO scores(identity_key, display_name, score, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(identity_key) DO UPDATE SET
                        display_name = excluded.display_name,
                        score = excluded.score,
                        updated_at = excluded.updated_at
                    """,
                    (identity_key, safe_display_name, score, now),
                )
                self._increment_metric("submitted_answers")
                if verdict == "correct":
                    self._increment_metric("correct_answers")
                elif verdict == "wrong":
                    self._increment_metric("wrong_answers")
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return delta

    async def add_result(self, identity_key: str, display_name: str, attempts: int) -> int:
        earned = {1: 3, 2: 2}.get(attempts, 1)
        return await self._apply_delta(identity_key, display_name, earned, "correct")

    async def add_penalty(self, identity_key: str, display_name: str) -> int:
        return await self._apply_delta(identity_key, display_name, WRONG_PENALTY, "wrong")

    def record_quiz_started(self, identity_key: str | None) -> None:
        if identity_key is None or not identity_key.strip():
            return
        cleaned = identity_key.strip()
        self._increment_metric("quiz_starts")
        self._conn.execute(
            "INSERT OR IGNORE INTO quiz_players(identity_key) VALUES (?)", (cleaned,)
        )

    def leaderboard(
        self,
        identity_key: str,
        top_n: int = 3,
        display_name: str | None = None,
    ) -> LeaderboardSnapshot:
        top = [
            self._score_entry_from_row(row)
            for row in self._conn.execute(
                """
                SELECT identity_key, display_name, score, updated_at
                FROM scores
                ORDER BY score DESC, updated_at ASC, identity_key ASC
                LIMIT ?
                """,
                (top_n,),
            )
        ]
        row = self._conn.execute(
            """
            SELECT identity_key, display_name, score, updated_at
            FROM scores
            WHERE identity_key = ?
            """,
            (identity_key,),
        ).fetchone()
        if row is None:
            my_entry = ScoreEntry(
                identity_key=identity_key,
                display_name=self._clean_display_name(display_name or identity_key),
                score=0,
                updated_at=self._now(),
            )
        else:
            my_entry = self._score_entry_from_row(row)
        return LeaderboardSnapshot(
            top=top,
            my_entry=my_entry,
            my_rank=self.rank_of(identity_key),
            week_started_at=self._week_started_at,
        )

    def rank_of(self, identity_key: str) -> int:
        row = self._conn.execute(
            "SELECT score, updated_at FROM scores WHERE identity_key = ?",
            (identity_key,),
        ).fetchone()
        if row is None:
            count = self._conn.execute("SELECT COUNT(*) AS count FROM scores").fetchone()
            return int(count["count"]) + 1
        preceding = self._conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM scores
            WHERE score > ?
               OR (score = ? AND updated_at < ?)
               OR (score = ? AND updated_at = ? AND identity_key < ?)
            """,
            (
                int(row["score"]),
                int(row["score"]),
                row["updated_at"],
                int(row["score"]),
                row["updated_at"],
                identity_key,
            ),
        ).fetchone()
        return int(preceding["count"]) + 1

    def stats(self, top_n: int = 3) -> dict[str, object]:
        top = [
            {
                "rank": rank,
                "display_name": row["display_name"],
                "score": int(row["score"]),
            }
            for rank, row in enumerate(
                self._conn.execute(
                    """
                    SELECT display_name, score
                    FROM scores
                    ORDER BY score DESC, updated_at ASC, identity_key ASC
                    LIMIT ?
                    """,
                    (top_n,),
                ),
                start=1,
            )
        ]
        participants = self._conn.execute("SELECT COUNT(*) AS count FROM scores").fetchone()
        players = self._conn.execute(
            "SELECT COUNT(*) AS count FROM quiz_players"
        ).fetchone()
        return {
            "participants": int(participants["count"]),
            "quiz_players": int(players["count"]),
            "quiz_starts": self._metric("quiz_starts"),
            "submitted_answers": self._metric("submitted_answers"),
            "correct_answers": self._metric("correct_answers"),
            "wrong_answers": self._metric("wrong_answers"),
            "top": top,
            "week_started_at": self._week_started_at.isoformat(),
        }

    async def snapshot_save(self) -> None:
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        rows = list(
            self._conn.execute(
                """
                SELECT identity_key, display_name, score, updated_at
                FROM scores
                ORDER BY score DESC, updated_at ASC, identity_key ASC
                """
            )
        )
        payload = {
            "version": 1,
            "week_started_at": self._week_started_at.isoformat(),
            "last_reset_at": self._meta("last_reset_at"),
            "entries": [
                self._score_entry_from_row(row).model_dump(mode="json") for row in rows
            ],
            "metrics": {
                "quiz_starts": self._metric("quiz_starts"),
                "submitted_answers": self._metric("submitted_answers"),
                "correct_answers": self._metric("correct_answers"),
                "wrong_answers": self._metric("wrong_answers"),
                "quiz_players": [
                    row["identity_key"]
                    for row in self._conn.execute(
                        "SELECT identity_key FROM quiz_players ORDER BY identity_key"
                    )
                ],
            },
        }
        tmp = self._snapshot_path.with_suffix(self._snapshot_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._snapshot_path)

    def snapshot_load(self) -> None:
        if not self._snapshot_path.exists():
            return
        try:
            payload = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                entries_payload = payload
                metrics = {}
            else:
                entries_payload = payload.get("entries", [])
                metrics = payload.get("metrics", {})
                if payload.get("week_started_at"):
                    self._week_started_at = datetime.fromisoformat(payload["week_started_at"])
                    self._set_meta("week_started_at", self._week_started_at.isoformat())
                if payload.get("last_reset_at"):
                    self._last_reset_at = datetime.fromisoformat(payload["last_reset_at"])
                    self._set_meta("last_reset_at", self._last_reset_at.isoformat())

            entries = [
                ScoreEntry.model_validate(item)
                for item in entries_payload
            ]
        except (json.JSONDecodeError, ValueError, TypeError):
            self._quarantine_snapshot()
            return

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for entry in entries:
                safe_display_name = self._clean_display_name(entry.display_name)
                self._conn.execute(
                    """
                    INSERT INTO scores(identity_key, display_name, score, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(identity_key) DO NOTHING
                    """,
                    (
                        entry.identity_key,
                        safe_display_name,
                        entry.score,
                        entry.updated_at.isoformat(),
                    ),
                )
            if isinstance(metrics, dict):
                for key in (
                    "quiz_starts",
                    "submitted_answers",
                    "correct_answers",
                    "wrong_answers",
                ):
                    self._conn.execute(
                        """
                        INSERT INTO metrics(key, value) VALUES (?, ?)
                        ON CONFLICT(key) DO NOTHING
                        """,
                        (key, int(metrics.get(key, 0))),
                    )
                raw_players = metrics.get("quiz_players", [])
                if isinstance(raw_players, list):
                    for item in raw_players:
                        identity_key = str(item).strip()
                        if identity_key:
                            self._conn.execute(
                                "INSERT OR IGNORE INTO quiz_players(identity_key) VALUES (?)",
                                (identity_key,),
                            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    async def maybe_weekly_reset(self, now: datetime) -> bool:
        boundary = self._reset_boundary(now)
        async with self._lock:
            last_reset = self._meta("last_reset_at")
            if last_reset is not None and datetime.fromisoformat(last_reset) >= boundary:
                return False
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE scores SET score = 0, updated_at = ?",
                    (boundary.isoformat(),),
                )
                self._conn.execute("DELETE FROM metrics")
                self._conn.execute("DELETE FROM quiz_players")
                self._set_meta("last_reset_at", boundary.isoformat())
                self._set_meta("week_started_at", boundary.isoformat())
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            self._last_reset_at = boundary
            self._week_started_at = boundary
            return True
