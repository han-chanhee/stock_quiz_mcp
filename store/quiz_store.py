"""모듈 C: quiz_id 인메모리 TTL 스토어.

단일 인스턴스/단일 이벤트루프 전제. Redis 금지(루트 규칙 — 오버엔지니어링).
동시 제출/중복 제출에 대비해 asyncio.Lock으로 보호한다.
"""

from __future__ import annotations

import asyncio
import secrets
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from contracts.schemas import QuizState, Verdict

_KST = timezone(timedelta(hours=9))

DEFAULT_TTL_SEC = 1800          # 30분
DEFAULT_MAX_ENTRIES = 10_000    # 메모리 폭주 방지 상한


def new_quiz_id() -> str:
    """추측 불가한 quiz_id. 다른 사용자가 정답 상태를 추측하기 어렵게 한다."""
    return secrets.token_urlsafe(8)


class QuizStore:
    """TTL 만료 + LRU 상한을 가진 quiz 상태 저장소.

    동기 인터페이스(put/get/update/purge_expired)는 계약 유지.
    중복 제출 원자 처리(퀴즈별 1회 정답)는 async compare_and_solve로 제공한다.
    """

    def __init__(
        self,
        ttl_sec: int = DEFAULT_TTL_SEC,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._ttl = timedelta(seconds=ttl_sec)
        self._max = max_entries
        self._data: OrderedDict[str, QuizState] = OrderedDict()
        self._lock = asyncio.Lock()

    # ── 내부 ─────────────────────────────────────────────────

    @staticmethod
    def _now() -> datetime:
        return datetime.now(_KST)

    def _expired(self, state: QuizState, now: datetime | None = None) -> bool:
        now = now or self._now()
        created = state.created_at
        # created_at이 naive면 KST로 간주해 비교(배치/테스트 혼용 대비)
        if created.tzinfo is None:
            created = created.replace(tzinfo=_KST)
        return now - created > self._ttl

    def _evict_if_needed(self) -> None:
        while len(self._data) > self._max:
            self._data.popitem(last=False)  # 가장 오래된 것부터 축출(LRU/FIFO)

    # ── 계약 인터페이스 ──────────────────────────────────────

    def put(self, state: QuizState) -> None:
        self._data[state.quiz_id] = state
        self._data.move_to_end(state.quiz_id)
        self._evict_if_needed()

    def get(self, quiz_id: str) -> QuizState | None:
        state = self._data.get(quiz_id)
        if state is None:
            return None
        if self._expired(state):
            del self._data[quiz_id]  # 만료 즉시 삭제
            return None
        self._data.move_to_end(quiz_id)
        return state

    def get_state_or_verdict(
        self, quiz_id: str
    ) -> tuple[QuizState | None, Verdict | None]:
        """조회하되 부재/만료를 구분한다.

        - 유효: (state, None)
        - 만료: (None, Verdict.EXPIRED)  — 만료 항목은 삭제
        - 부재: (None, Verdict.NOT_FOUND)
        """
        state = self._data.get(quiz_id)
        if state is None:
            return None, Verdict.NOT_FOUND
        if self._expired(state):
            del self._data[quiz_id]
            return None, Verdict.EXPIRED
        self._data.move_to_end(quiz_id)
        return state, None

    def update(self, state: QuizState) -> None:
        if state.quiz_id in self._data:
            self._data[state.quiz_id] = state
            self._data.move_to_end(state.quiz_id)

    def purge_expired(self) -> int:
        now = self._now()
        stale = [k for k, v in self._data.items() if self._expired(v, now)]
        for k in stale:
            del self._data[k]
        return len(stale)

    # ── 동시 제출 원자 처리 ──────────────────────────────────

    async def compare_and_solve(self, quiz_id: str) -> tuple[QuizState | None, bool]:
        """CORRECT 확정 직전 호출. 아직 미해결이면 solved=True로 원자 전환.

        반환: (state, was_first)
          - state None: 없음/만료
          - was_first True: 이번 호출이 이 퀴즈의 첫 정답 처리
          - was_first False: 이미 정답 처리된 상태
        """
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
        """오답 시 attempts 원자 증가. 없거나 만료면 None."""
        async with self._lock:
            state = self.get(quiz_id)
            if state is None:
                return None
            state.attempts += 1
            self.update(state)
            return state

    def __len__(self) -> int:
        return len(self._data)
