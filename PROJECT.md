# stock-quiz-mcp

카카오·카나나·ChatGPT에서 개인이 이용하는 주식 퀴즈 MCP 서버. PlayMCP 등록 대상.
컨셉: "주린이 대세 편승" — 퀴즈로 시장을 배우는 사전. 투자 권유 절대 금지.

## 아키텍처 한 줄 요약

사용자 요청은 캐시/스토어에서만 처리한다. 외부 API(KIS)는 배치와 리프레셔만 호출한다.
정답 채점은 서버가 한다(LLM에 위임 금지). quiz_id 기반 상태는 인메모리 TTL 스토어에 둔다.

## 절대 규칙 (모든 세션 공통)

1. `contracts/` 는 읽기 전용. 수정이 필요하면 작업을 멈추고 사용자에게 변경 제안만 한다.
2. 자기 모듈 폴더 밖의 파일을 생성/수정하지 않는다. (tests/ 내 자기 모듈 테스트는 예외)
3. 모듈 간 직접 import 금지. 오직 `contracts.schemas` 의 Pydantic 모델만 공유한다.
   의존 방향: server → services → (clients, store). 역방향 금지.
4. "kakao" 문자열을 서버명/툴명/description 어디에도 넣지 않는다 (대소문자 무관).
5. 툴 이름 규칙: 영문/숫자/underscore/hyphen, 128자 이내, 중복 금지.
6. 툴 description: 영문 작성, "Stock Quiz Dictionary(주식사전 퀴즈)" 병기, 1,024자 이내.
7. annotations 5개(title, readOnlyHint, destructiveHint, openWorldHint, idempotentHint) 전부 명시.
8. 어떤 출력에도 매수/매도 권유 문장을 생성하지 않는다. 미니분석은 팩트만.
   근거 없는 원인은 반드시 "특별한 재료 확인 안 됨"으로 반환한다.
9. 모든 툴 응답은 정제된 마크다운. 외부 API 원본 JSON을 그대로 반환하지 않는다.
10. 주석/독스트링/커밋 메시지는 한국어.
11. 런타임 실시간 뉴스 검색 금지. reason은 배치 프리캐싱(Reason 모델, 출처 URL 필수)
    조회 전용. 미조회 시 "특별한 재료 확인 안 됨".
12. 힌트는 출제 시점에 전 단계를 사전 생성해 QuizState.hints_precomputed에 저장.
    채점 경로에 문자열 연산 금지 (price UP/DOWN 비교 1회만 예외).
13. 서버 기동 시 batch/data/*.json 전체를 model_validate로 검증. 실패 시 기동 중단.
    썩은 데이터로 조용히 서비스하는 것이 최악의 오류다.
14. 조립 이후 발견된 버그는 재현 테스트 작성 → 수정 순서 강제. 테스트 없는 핫픽스 금지.

## 성능 제약 (PlayMCP 필수)

- 응답속도 평균 100ms, p99 3,000ms.
- 달성 방법: 사용자 요청 경로에서 외부 API 호출 금지. 랭킹/시세는 캐시에서 조립만.
- 예외 없음. "이번 한 번만 실시간 호출" 금지.
- 리프레셔는 장중 3회(10:00 / 13:00 / 15:40)만 실행. 퀴즈는 출제 시점 고정이
  컨셉이므로 실시간성이 필요 없다. KIS 호출량 = 하루 수십 회 상수.

## 모듈 구성 (병렬 세션 5개)

| 모듈 | 폴더 | 담당 | 선행조건 |
|---|---|---|---|
| A | clients/ | KIS 시세/랭킹 API 래퍼 + mock 픽스처 | contracts 확정 |
| B | services/ | 퀴즈 출제/채점/힌트/미니분석 로직 | contracts + A의 mock |
| C | store/ | quiz_id 인메모리 TTL 스토어 | contracts 확정 |
| D | batch/ | 종목풀/등락률 스냅샷 일일 갱신 | contracts + A의 mock |
| E | server/ | FastMCP 엔트리, 툴 5개 등록, 캐시 | contracts + B/C 인터페이스 |

각 모듈의 상세 지시는 해당 폴더의 CLAUDE.md 참조.

## 완료 정의 (게이트 0)

- 각 모듈은 mock 기반 pytest 통과가 완료 조건이다. 테스트는 `tests/test_<모듈>.py`.
- 실 API 키 없이 전체 테스트가 통과해야 한다 (mock 주입 구조 필수).

## 개발 순서

1. contracts/schemas.py + tool_specs.md 확정 (완료됨, 읽기 전용)
2. 모듈 A~E 병렬 개발 (세션 분리)
3. 조립: server의 의존성 주입 지점에서 mock → 실 구현 교체
4. 게이트 1~4 검증 (tests/GATES.md 참조)
5. Docker 패키징 → 배포 → MCP Inspector 원격 검증 → PlayMCP 등록

## 기술 스택

- Python 3.12+, FastMCP (Streamable HTTP, stateless_http=True)
- Pydantic v2, httpx (async), pytest + pytest-asyncio
- 저장소: 인메모리 dict + TTL (Redis 금지 — 오버엔지니어링)
- 배치 산출물: batch/data/*.json (DB 금지)
