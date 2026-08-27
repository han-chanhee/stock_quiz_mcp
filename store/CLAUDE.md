# 모듈 C: store — quiz_id 상태 저장소

## 담당

- `quiz_store.py`: 인메모리 TTL 스토어 (단일 인스턴스 전제, Redis 금지)

## 인터페이스 (유지할 것)

```python
class QuizStore:
    def put(self, state: QuizState) -> None: ...
    def get(self, quiz_id: str) -> QuizState | None: ...   # 만료면 None + 내부 삭제
    def update(self, state: QuizState) -> None: ...        # attempts/solved 갱신
    def purge_expired(self) -> int: ...                    # 백그라운드 정리, 삭제 수 반환
```

## 요구사항

- TTL 30분 (created_at 기준)
- quiz_id 생성: `secrets.token_urlsafe(8)` — 다른 사용자가 정답 상태를 추측하기 어렵게 해야 함
- asyncio 환경에서 안전할 것 (동시 제출 대비 lock 또는 단일 이벤트루프 전제 명시)
- 최대 보관 개수 상한 (예: 10,000) 초과 시 오래된 것부터 제거 — 메모리 폭주 방지

## 완료 정의

- `tests/test_store.py` 통과: put/get 왕복, TTL 만료 후 None, solved 갱신,
  상한 초과 시 축출, 동시 update 경합 테스트(asyncio.gather)
