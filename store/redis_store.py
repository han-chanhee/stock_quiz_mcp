"""Redis/Valkey 기반 분산 런타임 저장소.

PlayMCP 컨테이너가 여러 개로 늘어나도 퀴즈 상태와 랭킹을 같은 외부 저장소에
묶기 위한 운영 백엔드다. redis-py는 실제 Redis 모드에서만 lazy import한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from contracts.schemas import LeaderboardSnapshot, QuizState, ScoreEntry, Verdict

from .quiz_store import DEFAULT_TTL_SEC, QuizStore
from .score_store import ScoreStore, WRONG_PENALTY

_KST = timezone(timedelta(hours=9))
DEFAULT_REDIS_PREFIX = "stockquiz"
_RANK_SCORE_SCALE = 1_000_000_000


def _redis_from_url(redis_url: str):
    try:
        from redis import Redis
    except ImportError as exc:
        raise RuntimeError(
            "STATE_BACKEND=redis requires the 'redis' package. "
            "Install requirements.txt in the deployment image."
        ) from exc
    return Redis.from_url(redis_url, decode_responses=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_KST)
    return value


class RedisQuizStore(QuizStore):
    """TTL + Redis 저장 퀴즈 상태소."""

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        client: Any | None = None,
        key_prefix: str = DEFAULT_REDIS_PREFIX,
        ttl_sec: int = DEFAULT_TTL_SEC,
        max_entries: int = 5_000_000,
    ) -> None:
        if client is None and not redis_url:
            raise ValueError("redis_url is required when client is not provided")
        self._client = client or _redis_from_url(str(redis_url))
        self._prefix = key_prefix.rstrip(":")
        self._ttl_sec = ttl_sec
        self._ttl = timedelta(seconds=ttl_sec)
        self._max = max_entries
        self._lock = asyncio.Lock()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(_KST)

    def _key(self, quiz_id: str) -> str:
        return f"{self._prefix}:quiz:{quiz_id}"

    @property
    def _updated_index(self) -> str:
        return f"{self._prefix}:quiz:index:updated"

    @property
    def _created_index(self) -> str:
        return f"{self._prefix}:quiz:index:created"

    def _expired(self, state: QuizState, now: datetime | None = None) -> bool:
        return (now or self._now()) - _aware(state.created_at) > self._ttl

    def _load(self, quiz_id: str) -> QuizState | None:
        payload = self._client.get(self._key(quiz_id))
        if payload is None:
            self._client.zrem(self._updated_index, quiz_id)
            self._client.zrem(self._created_index, quiz_id)
            return None
        state = QuizState.model_validate_json(payload)
        if self._expired(state):
            self._client.delete(self._key(quiz_id))
            self._client.zrem(self._updated_index, quiz_id)
            self._client.zrem(self._created_index, quiz_id)
            return None
        return state

    def _save(self, state: QuizState) -> None:
        now = self._now().timestamp()
        self._client.set(self._key(state.quiz_id), state.model_dump_json(), ex=self._ttl_sec)
        self._client.zadd(self._updated_index, {state.quiz_id: now})
        self._client.zadd(self._created_index, {state.quiz_id: _aware(state.created_at).timestamp()})
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        overflow = len(self) - self._max
        if overflow <= 0:
            return
        victims = self._client.zrange(self._updated_index, 0, overflow - 1)
        if not victims:
            return
        keys = [self._key(quiz_id) for quiz_id in victims]
        self._client.delete(*keys)
        self._client.zrem(self._updated_index, *victims)
        self._client.zrem(self._created_index, *victims)

    def put(self, state: QuizState) -> None:
        self._save(state)

    def get(self, quiz_id: str) -> QuizState | None:
        state = self._load(quiz_id)
        if state is not None:
            self._client.zadd(self._updated_index, {quiz_id: self._now().timestamp()})
        return state

    def get_state_or_verdict(
        self, quiz_id: str
    ) -> tuple[QuizState | None, Verdict | None]:
        payload = self._client.get(self._key(quiz_id))
        if payload is None:
            self._client.zrem(self._updated_index, quiz_id)
            self._client.zrem(self._created_index, quiz_id)
            return None, Verdict.NOT_FOUND
        state = QuizState.model_validate_json(payload)
        if self._expired(state):
            self._client.delete(self._key(quiz_id))
            self._client.zrem(self._updated_index, quiz_id)
            self._client.zrem(self._created_index, quiz_id)
            return None, Verdict.EXPIRED
        self._client.zadd(self._updated_index, {quiz_id: self._now().timestamp()})
        return state, None

    def update(self, state: QuizState) -> None:
        self._save(state)

    def purge_expired(self) -> int:
        threshold = (self._now() - self._ttl).timestamp()
        victims = self._client.zrangebyscore(self._created_index, "-inf", threshold)
        if not victims:
            return 0
        self._client.delete(*(self._key(quiz_id) for quiz_id in victims))
        self._client.zrem(self._updated_index, *victims)
        self._client.zrem(self._created_index, *victims)
        return len(victims)

    async def compare_and_solve(self, quiz_id: str) -> tuple[QuizState | None, bool]:
        from redis.exceptions import WatchError

        key = self._key(quiz_id)
        async with self._lock:
            while True:
                with self._client.pipeline() as pipe:
                    try:
                        pipe.watch(key)
                        payload = pipe.get(key)
                        if payload is None:
                            pipe.unwatch()
                            return None, False
                        state = QuizState.model_validate_json(payload)
                        if self._expired(state):
                            pipe.multi()
                            pipe.delete(key)
                            pipe.zrem(self._updated_index, quiz_id)
                            pipe.zrem(self._created_index, quiz_id)
                            pipe.execute()
                            return None, False
                        if state.solved:
                            pipe.unwatch()
                            return state, False
                        state.solved = True
                        pipe.multi()
                        pipe.set(key, state.model_dump_json(), ex=self._ttl_sec)
                        pipe.zadd(self._updated_index, {quiz_id: self._now().timestamp()})
                        pipe.execute()
                        return state, True
                    except WatchError:
                        continue

    async def record_attempt(self, quiz_id: str) -> QuizState | None:
        from redis.exceptions import WatchError

        key = self._key(quiz_id)
        async with self._lock:
            while True:
                with self._client.pipeline() as pipe:
                    try:
                        pipe.watch(key)
                        payload = pipe.get(key)
                        if payload is None:
                            pipe.unwatch()
                            return None
                        state = QuizState.model_validate_json(payload)
                        if self._expired(state):
                            pipe.multi()
                            pipe.delete(key)
                            pipe.zrem(self._updated_index, quiz_id)
                            pipe.zrem(self._created_index, quiz_id)
                            pipe.execute()
                            return None
                        state.attempts += 1
                        pipe.multi()
                        pipe.set(key, state.model_dump_json(), ex=self._ttl_sec)
                        pipe.zadd(self._updated_index, {quiz_id: self._now().timestamp()})
                        pipe.execute()
                        return state
                    except WatchError:
                        continue

    def __len__(self) -> int:
        return int(self._client.zcard(self._updated_index))


class RedisScoreStore(ScoreStore):
    """Redis sorted-set 기반 주간 점수/랭킹 저장소."""

    _APPLY_DELTA_LUA = """
local ranking = KEYS[1]
local scores = KEYS[2]
local names = KEYS[3]
local updated = KEYS[4]
local metrics = KEYS[5]
local seq_key = KEYS[6]
local identity = ARGV[1]
local display_name = ARGV[2]
local delta = tonumber(ARGV[3])
local verdict = ARGV[4]
local now = ARGV[5]
local scale = tonumber(ARGV[6])

local current = tonumber(redis.call('HGET', scores, identity) or '0') + delta
redis.call('HSET', scores, identity, current)
redis.call('HSET', names, identity, display_name)
redis.call('HSET', updated, identity, now)
local seq = redis.call('INCR', seq_key)
redis.call('ZADD', ranking, current * scale - seq, identity)
redis.call('HINCRBY', metrics, 'submitted_answers', 1)
if verdict == 'correct' then
  redis.call('HINCRBY', metrics, 'correct_answers', 1)
elseif verdict == 'wrong' then
  redis.call('HINCRBY', metrics, 'wrong_answers', 1)
end
return current
"""

    _RESET_LUA = """
local last_reset = KEYS[1]
local week_started = KEYS[2]
local boundary = ARGV[1]
local current = redis.call('GET', last_reset)
if current and current >= boundary then
  return 0
end
redis.call('SET', last_reset, boundary)
redis.call('SET', week_started, boundary)
for i = 3, #KEYS do
  redis.call('DEL', KEYS[i])
end
return 1
"""

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        client: Any | None = None,
        key_prefix: str = DEFAULT_REDIS_PREFIX,
        reset_weekday: int = 0,
        reset_hour: int = 0,
    ) -> None:
        super().__init__(reset_weekday=reset_weekday, reset_hour=reset_hour)
        if client is None and not redis_url:
            raise ValueError("redis_url is required when client is not provided")
        self._client = client or _redis_from_url(str(redis_url))
        self._prefix = key_prefix.rstrip(":")
        self._lock = asyncio.Lock()
        stored_week = self._client.get(self._key("meta:week_started_at"))
        if stored_week:
            self._week_started_at = datetime.fromisoformat(stored_week)
        else:
            self._client.set(self._key("meta:week_started_at"), self._week_started_at.isoformat())

    def _key(self, name: str) -> str:
        return f"{self._prefix}:score:{name}"

    def _metric(self, name: str) -> int:
        value = self._client.hget(self._key("metrics"), name)
        return int(value) if value is not None else 0

    def _entry(self, identity_key: str) -> ScoreEntry | None:
        score = self._client.hget(self._key("scores"), identity_key)
        if score is None:
            return None
        display_name = self._client.hget(self._key("names"), identity_key) or identity_key
        updated_at = self._client.hget(self._key("updated"), identity_key)
        return ScoreEntry(
            identity_key=identity_key,
            display_name=display_name,
            score=int(score),
            updated_at=datetime.fromisoformat(updated_at) if updated_at else self._now(),
        )

    def display_name_for(self, identity_key: str) -> str | None:
        value = self._client.hget(self._key("names"), identity_key)
        return str(value) if value is not None else None

    async def _apply_delta(
        self,
        identity_key: str,
        display_name: str,
        delta: int,
        verdict: str,
    ) -> int:
        safe_display_name = self._clean_display_name(display_name)
        async with self._lock:
            self._client.eval(
                self._APPLY_DELTA_LUA,
                6,
                self._key("ranking"),
                self._key("scores"),
                self._key("names"),
                self._key("updated"),
                self._key("metrics"),
                self._key("seq"),
                identity_key,
                safe_display_name,
                delta,
                verdict,
                self._now().isoformat(),
                _RANK_SCORE_SCALE,
            )
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
        self._client.hincrby(self._key("metrics"), "quiz_starts", 1)
        self._client.sadd(self._key("quiz_players"), cleaned)

    def leaderboard(
        self,
        identity_key: str,
        top_n: int = 3,
        display_name: str | None = None,
    ) -> LeaderboardSnapshot:
        top_ids = self._client.zrevrange(self._key("ranking"), 0, top_n - 1)
        top = [entry for item in top_ids if (entry := self._entry(str(item))) is not None]
        my_entry = self._entry(identity_key)
        if my_entry is None:
            my_entry = ScoreEntry(
                identity_key=identity_key,
                display_name=self._clean_display_name(display_name or identity_key),
                score=0,
                updated_at=self._now(),
            )
        return LeaderboardSnapshot(
            top=top,
            my_entry=my_entry,
            my_rank=self.rank_of(identity_key),
            week_started_at=self._week_started_at,
        )

    def rank_of(self, identity_key: str) -> int:
        rank = self._client.zrevrank(self._key("ranking"), identity_key)
        if rank is None:
            return int(self._client.zcard(self._key("ranking"))) + 1
        return int(rank) + 1

    def stats(self, top_n: int = 3) -> dict[str, object]:
        top = [
            {
                "rank": rank,
                "display_name": entry.display_name,
                "score": entry.score,
            }
            for rank, entry in enumerate(self.leaderboard("", top_n=top_n).top, start=1)
        ]
        return {
            "participants": int(self._client.zcard(self._key("ranking"))),
            "quiz_players": int(self._client.scard(self._key("quiz_players"))),
            "quiz_starts": self._metric("quiz_starts"),
            "submitted_answers": self._metric("submitted_answers"),
            "correct_answers": self._metric("correct_answers"),
            "wrong_answers": self._metric("wrong_answers"),
            "top": top,
            "week_started_at": self._week_started_at.isoformat(),
        }

    async def snapshot_save(self) -> None:
        return None

    def snapshot_load(self) -> None:
        return None

    async def maybe_weekly_reset(self, now: datetime) -> bool:
        boundary = self._reset_boundary(now)
        async with self._lock:
            did_reset = self._client.eval(
                self._RESET_LUA,
                9,
                self._key("meta:last_reset_at"),
                self._key("meta:week_started_at"),
                self._key("ranking"),
                self._key("scores"),
                self._key("names"),
                self._key("updated"),
                self._key("metrics"),
                self._key("quiz_players"),
                self._key("seq"),
                boundary.isoformat(),
            )
        if int(did_reset) != 1:
            return False
        self._last_reset_at = boundary
        self._week_started_at = boundary
        return True
