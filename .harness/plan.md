# 작업 계획

> Claude Code가 초안을 만들고 사람이 승인합니다. Codex는 이 파일의 태스크 단위로만 작업합니다.

## 목표

매 퀴즈 정답 제출 시 시도 횟수에 따라 차등 점수(1회 3점/2회 2점/3회 이상 1점)를 부여하고,
TOP5 + 본인의 정확한 순위를 응답에 표시하는 랭킹 시스템을 nickname 기반으로 구현한다.
순위는 매주 초기화된다. OAuth 인증서버는 코드까지 준비하되, 실제 활성화(카카오
개인정보보호팀 동의문 승인)는 이 계획의 완료 조건에 포함하지 않는다.

## 배경 (이 계획이 필요한 이유)

- 사용자 확정 요구사항: 점수는 시도 횟수 차등(1회=3점/2회=2점/3회 이상=1점, 오답=0점),
  매 정답 제출 응답에 TOP5+본인 정확한 순위 숫자 표시, 주간 리셋.
- 이 서버는 원래 "카카오톡 단체방" 컨셉으로 문서화되어 있었으나(`store/CLAUDE.md`의
  "단체방 부정행위 방지" 등) 실제 PlayMCP 배포는 **1:1 대화 구조**임이 이번 세션에서
  확인됨. 기존 `submit_answer`의 `compare_and_solve`(선착순 1명만 정답 처리)는
  이 전제와 무관하게 시그니처를 유지하되, 점수는 "선착순"이 아니라 "실제로 맞힌
  이번 제출"에 부여한다.
- 유저 식별은 OAuth 없이 `nickname` 파라미터로 한다(카카오 공식 가이드 확인 결과,
  OAuth는 우리가 직접 인증서버를 구축해야 하고 개인정보 제3자 제공 동의문을
  디스코드로 제출해 카카오 개인정보보호팀 검토·승인을 받아야 함 — 승인이 나면
  즉시 켤 수 있도록 코드는 이번에 준비하되, 활성화는 승인 이후로 분리).
- 저장소는 HANDOFF.md 원안대로 인메모리 dict + 주기적 JSON 스냅샷(Redis/DB 금지 유지).
- `contracts/schemas.py`에 `ScoreEntry`, `LeaderboardSnapshot` 모델 추가를 사용자에게
  제안해 승인받음(이 문서 하단 TASK-001 참고, 절대 규칙 준수 — 읽기전용 파일이라
  Claude Code가 계획 승인과 함께 직접 반영한다).

## 공통 제약

- 건드리면 안 되는 경로: `contracts/schemas.py`는 TASK-001에서만, 그것도 Claude Code가
  직접 편집(Codex 작업 대상 아님). 그 외 태스크에서는 `contracts/` 전체 수정 금지.
- 추가 금지 의존성: 없음. `bisect`(표준 라이브러리)만 사용. `sortedcontainers` 등
  이미 venv에 설치돼 있어도(fastmcp 전이 의존성) `requirements.txt`/`pyproject.toml`에
  직접 선언 금지.
- Redis/외부 DB 금지 — 인메모리 dict + JSON 스냅샷만.
- 랭킹 저장 경로는 `batch/data/`(배치 전용 디렉터리) 밖에 둔다 — 신규 `store/data/` 사용.
- 요청 경로(퀴즈 출제/채점)에 외부 API 호출 추가 금지 원칙 유지.
- 주석/독스트링/커밋 메시지는 한국어.
- 기존 테스트 수정·삭제 금지(추가만).

---

## TASK-001: contracts/schemas.py에 랭킹 모델 추가 (Claude Code 직접 편집)

- **depends_on**: none
- **수정 대상 파일**:
  - `contracts/schemas.py` (수정 — Claude Code가 직접 편집, Codex 작업 대상 아님)
- **인터페이스 확정**:
  ```python
  class ScoreEntry(BaseModel):
      """랭킹 1인분 기록. 주간 리셋 시 전원 초기화된다.

      identity_key는 nickname 또는 (향후 OAuth 도입 시) OAuth user_id.
      현재는 nickname만 사용하며, 서로 다른 식별 수단은 별도 키로 취급한다.
      """
      identity_key: str
      display_name: str
      score: int = 0
      updated_at: datetime

  class LeaderboardSnapshot(BaseModel):
      """submit_answer 응답에 실리는 랭킹 요약. 정답 확정 시에만 생성된다."""
      top: list[ScoreEntry]
      my_entry: ScoreEntry
      my_rank: int
      week_started_at: datetime
  ```
- **구현 내용**:
  1. 위 두 모델을 `contracts/schemas.py`의 "퀴즈 상태" 섹션과 "툴 응답" 섹션 사이
     (또는 "툴 응답" 섹션 상단)에 추가한다. 기존 `Hint`/`QuizState` 스타일(필드별
     인라인 주석)을 따른다.
  2. 기존 모델(`GradingResult` 등)은 이 태스크에서 건드리지 않는다 — 필드 추가는
     TASK-003에서 `SubmitOutcome`(dataclass, contracts 밖)에 한다.
- **검증 명령**:
  ```
  python -c "from contracts.schemas import ScoreEntry, LeaderboardSnapshot; print('ok')"
  pytest -q
  ```
- **완료 조건**: import 성공, 기존 전체 테스트 회귀 없음(pytest -q 그대로 통과)
- **금지 사항**:
  - 기존 모델의 필드를 변경·삭제하지 말 것
  - 이 태스크는 Codex에게 위임하지 않는다(계획 승인 직후 Claude Code가 직접 편집)

---

## TASK-002: store/score_store.py 신설 — 랭킹 저장소

- **depends_on**: TASK-001
- **수정 대상 파일**:
  - `store/score_store.py` (신규)
  - `store/__init__.py` (수정 — export 추가)
  - `tests/test_score_store.py` (신규)
- **인터페이스 확정**:
  ```python
  DEFAULT_SNAPSHOT_PATH = Path(__file__).parent / "data" / "scores.json"
  DEFAULT_RESET_WEEKDAY = 0   # 월요일 (datetime.weekday() 기준 0=월)
  DEFAULT_RESET_HOUR = 0      # 00:00 KST — 실제 값은 팀 확인 필요, 임시 기본값

  class ScoreStore:
      def __init__(
          self,
          snapshot_path: Path | None = None,
          reset_weekday: int = DEFAULT_RESET_WEEKDAY,
          reset_hour: int = DEFAULT_RESET_HOUR,
      ) -> None: ...

      async def add_result(self, identity_key: str, display_name: str, attempts: int) -> int:
          """정답 확정 시 호출. attempts(1부터)로 점수를 계산해 가산하고 이번에 획득한 점수를 반환한다.
          1회=3점, 2회=2점, 3회 이상=1점."""
          ...

      def leaderboard(self, identity_key: str, top_n: int = 5) -> LeaderboardSnapshot:
          """TOP N + 본인 정확한 순위. identity_key가 아직 없으면 score=0인 엔트리로 취급."""
          ...

      def rank_of(self, identity_key: str) -> int:
          """1-based 순위. 미기록 유저는 전체 인원+1위로 취급(최하위)."""
          ...

      async def snapshot_save(self) -> None: ...
      def snapshot_load(self) -> None: ...
      async def maybe_weekly_reset(self, now: datetime) -> bool:
          """now가 리셋 시점을 지났고 아직 이번 주기에 리셋 안 했으면 전원 초기화. 리셋 여부를 반환."""
          ...
  ```
- **구현 내용**:
  1. 점수 계산 규칙(1회=3, 2회=2, 3회 이상=1)을 `_SCORE_TABLE` 같은 모듈 상수로 고정한다.
  2. 자료구조: `dict[identity_key, ScoreEntry]` + 점수 내림차순 정렬 리스트를
     `bisect`로 유지(삽입/갱신 시 O(log n) 탐색 위치 계산 + O(n) 리스트 시프트).
     동점 처리: 동점자는 먼저 그 점수에 도달한 사람이 더 높은 순위(= `updated_at`
     오름차순을 2차 정렬 키로).
  3. `store/quiz_store.py`의 `asyncio.Lock` 패턴을 재사용(새 Lock 인스턴스, `quiz_store.py`
     자체는 수정하지 않음)해 `add_result`/`maybe_weekly_reset`을 원자적으로 만든다.
  4. `snapshot_save`/`snapshot_load`는 `store/data/scores.json`에 `[ScoreEntry, ...]`를
     JSON으로 직렬화/역직렬화(`model_dump(mode="json")`/`model_validate`). 디렉터리가
     없으면 생성한다.
  5. `maybe_weekly_reset`은 마지막 리셋 시각(`_last_reset_at`)을 내부 상태로 갖고,
     `now`가 `reset_weekday`/`reset_hour` 이후이고 마지막 리셋이 그 이전이면 전원
     점수를 0으로 초기화하고 `_last_reset_at`을 갱신 후 `True`를 반환한다.
  6. `store/__init__.py`에 `ScoreStore` export를 추가한다(기존 `QuizStore` export는
     유지).
- **검증 명령**:
  ```
  pytest tests/test_score_store.py -q
  ```
- **완료 조건**: 아래 케이스 포함 전체 통과
  - 1회/2회/3회 이상 정답 시 점수 차등 부여 정확성
  - `leaderboard()`가 TOP5 + 본인 정확한 순위를 반환(동점자 순위 규칙 포함)
  - 미기록 유저의 `rank_of`가 "전체 인원+1"
  - `maybe_weekly_reset` 이후 전원 0점, 재호출 시 같은 주기 내 중복 리셋 안 됨
  - `snapshot_save` → 새 `ScoreStore` 인스턴스에서 `snapshot_load` → 데이터 왕복 일치
- **금지 사항**:
  - `store/quiz_store.py` 수정 금지
  - `sortedcontainers` 등 외부 패키지 추가 금지 (`bisect` 표준 라이브러리만)
  - Redis/DB 사용 금지
  - `batch/data/` 경로에 스냅샷을 쓰지 말 것(배치 전용 디렉터리와 책임 분리)

---

## TASK-003: handlers.py에 nickname·점수·랭킹 연동

- **depends_on**: TASK-002
- **수정 대상 파일**:
  - `server/handlers.py` (수정)
  - `tests/test_server.py` (수정 — 케이스 추가)
- **인터페이스 확정**:
  ```python
  class QuizHandlers:
      def __init__(
          self,
          cache: QuizCache,
          store: QuizStore,
          score_store: ScoreStore,
          bank: QuizBank | None = None,
          rng: random.Random | None = None,
      ) -> None: ...

      def quiz(
          self,
          mode: QuizMode,
          nickname: str,
          market: Market = Market.KR,
          period: Period = Period.TODAY,
          sector: Sector | None = None,
      ) -> QuizOutcome: ...

      async def submit_answer(self, quiz_id: str, answer: str, nickname: str) -> SubmitOutcome: ...

  @dataclass
  class SubmitOutcome:
      verdict: Verdict
      markdown: str
      analysis: MiniAnalysis | None = None
      attempts: int = 0
      next_actions: list[str] = field(default_factory=list)
      leaderboard: LeaderboardSnapshot | None = None  # CORRECT 확정 시에만 채워짐
  ```
- **구현 내용**:
  1. `quiz()`는 `nickname`을 받지만 이번 태스크에서는 사용처가 없다(출제 시점엔
     점수 로직 없음) — 향후 "출제 이력" 기능 대비 시그니처만 통일. 현재 구현
     본문은 무시해도 되지만 파라미터는 반드시 받는다(다음 태스크에서 `main.py`가
     그대로 넘긴다).
  2. `submit_answer`의 CORRECT 확정 분기(`was_first=True`, 기존 L207 근방)에서
     `attempts`(`solved_state.attempts`)를 이용해
     `score_store.add_result(nickname, nickname, solved_state.attempts)`를 호출하고,
     반환된 획득 점수와 `score_store.leaderboard(nickname)`을 `SubmitOutcome.leaderboard`에
     채운다. `was_first=False`(이미 solved) 분기에서는 점수 재부여·랭킹 조회를
     하지 않는다(중복 방지).
  3. `_render_correct`에 랭킹 섹션을 추가한다 — TOP5를 순위 1~5로 나열하고,
     본인이 TOP5 밖이면 "당신은 N위" 형태로 별도 줄에 표시. 본인이 TOP5 안이면
     중복 표시하지 않는다.
  4. 오답(`Verdict.WRONG`) 응답에는 랭킹을 표시하지 않는다(점수 미확정 상태 노출 방지).
  5. `nickname`이 빈 문자열이거나 공백만인 경우: 점수 부여를 하지 않고 랭킹 없이
     정답 축하만 표시한다(크래시 대신 안내 — 기존 `guess_company`의 섹터 없음
     가드와 동일한 방어적 스타일).
- **검증 명령**:
  ```
  pytest tests/test_server.py -q
  ```
- **완료 조건**: 위 명령 종료 코드 0. 신규 케이스:
  - 1회 정답 시 응답에 "3점" 및 TOP5/본인 순위가 포함됨
  - 이미 solved된 퀴즈에 재제출 시 점수 중복 미부여
  - 오답 응답에 랭킹 섹션 없음
  - nickname 미입력 시 크래시 없이 정답 처리만 됨(점수 미부여)
- **금지 사항**:
  - `compare_and_solve`/`record_attempt`(store/quiz_store.py) 시그니처 변경 금지
  - `services/` 모듈을 통하지 않고 `store`를 우회 직접 조작 금지(의존 방향 유지)
  - 매수/매도 권유 문장 추가 금지(기존 원칙 유지)

---

## TASK-004: main.py 조립 — score_store 주입 + 주간 리셋/스냅샷 백그라운드 태스크

- **depends_on**: TASK-002, TASK-003
- **수정 대상 파일**:
  - `server/main.py` (수정)
  - `tests/test_server.py` (수정 — 케이스 추가)
- **인터페이스 확정**:
  ```python
  def build_app(
      cache: QuizCache,
      store: QuizStore,
      score_store: ScoreStore,
      bank: QuizBank | None = None,
      refresh_client: MarketClient | None = None,
  ) -> FastMCP: ...

  _SNAPSHOT_INTERVAL_SEC: int = 300  # 5분마다 랭킹 스냅샷 저장

  async def _weekly_reset_loop(score_store: ScoreStore) -> None: ...
  async def _snapshot_loop(score_store: ScoreStore) -> None: ...
  ```
- **구현 내용**:
  1. `build_app`에 `score_store` 필수 인자를 추가하고 `QuizHandlers(cache, store,
     score_store, bank)`로 전달한다.
  2. `quiz`/`submit_answer` 툴 정의에 `nickname: str` 파라미터를 추가하고
     `handlers.quiz(mode, nickname, market, period, sector)` /
     `handlers.submit_answer(quiz_id, answer, nickname)`으로 그대로 전달한다.
     툴 `description`에 nickname이 필수 파라미터임을 영/국문 병기로 명시한다
     (기존 description 스타일 유지, 1,024자 이내).
  3. `_lifespan`에 `_refresher_loop`와 나란히
     `asyncio.create_task(_weekly_reset_loop(score_store))`,
     `asyncio.create_task(_snapshot_loop(score_store))`를 등록하고, 종료 시
     함께 `cancel()` + `await`로 정리한다(기존 `task.cancel()` 패턴 재사용).
  4. `_weekly_reset_loop`는 60초 간격으로 `score_store.maybe_weekly_reset(_now_kst())`를
     호출하는 단순 루프(기존 `_is_market_open` 같은 장중 가드 불필요 — 리셋은
     24시간 아무 때나 체크 가능).
  5. `_snapshot_loop`는 `_SNAPSHOT_INTERVAL_SEC`(5분) 간격으로
     `score_store.snapshot_save()`를 호출한다.
  6. `create_server()`에서 `ScoreStore()` 생성 후 `score_store.snapshot_load()`로
     기존 스냅샷을 복원하고 `build_app(..., score_store=score_store, ...)`에 주입한다.
- **검증 명령**:
  ```
  pytest tests/test_server.py -q
  ```
- **완료 조건**: 위 명령 종료 코드 0. `build_app`이 `score_store` 없이 호출되면
  `TypeError`(필수 인자 누락)로 즉시 드러남을 테스트로 확인.
- **금지 사항**:
  - `_refresher_loop`/`_refresh_today`/`_is_market_open` 로직 변경 금지
  - 리셋 주기·스냅샷 주기를 하드코딩된 매직넘버로 흩뿌리지 말 것(상수로만 관리)
  - `/health` 응답 구조 변경 금지

---

## TASK-005: OAuth 인증서버 코드 준비 (비활성 상태로 스캐폴딩만)

- **depends_on**: none (TASK-001~004와 독립, 병렬 가능)
- **배경**: 카카오 공식 가이드(`Kakao Tools 개발 가이드.pdf` 6장) 확인 결과 — OAuth는
  외부 IdP 위임이 아니라 우리가 직접 인증서버(consent 화면, 토큰 발급, Redirect URI
  처리)를 호스팅해야 한다. FastMCP 3.x가 `fastmcp.server.auth`에 완성된 프레임워크를
  제공하고 필요 패키지(`authlib`, `joserfc`, `pyjwt`)는 이미 venv에 설치돼 있다(fastmcp
  전이 의존성). 이 태스크는 **코드만 준비**하고, 실제 배포 활성화는 개인정보 제3자
  제공 동의문을 디스코드로 제출해 카카오 개인정보보호팀 승인을 받은 뒤 별도로 진행한다
  (이 계획의 완료 조건에 "카카오 승인"은 포함하지 않는다 — 코드가 있어도 기본값은
  비활성).
- **수정 대상 파일**:
  - `server/auth.py` (신규)
  - `server/main.py` (수정 — 환경변수로 조건부 활성화 지점만 추가)
  - `tests/test_auth.py` (신규)
- **인터페이스 확정**:
  ```python
  # server/auth.py
  def build_auth_provider() -> "AuthProvider | None":
      """OAUTH_ENABLED=1 환경변수가 설정된 경우에만 인증 프로바이더를 구성해 반환한다.
      미설정 시 None(비활성 — 지금 배포 기본값)."""
      ...

  # server/main.py 변경 지점
  # build_app() 시그니처는 변경하지 않는다. create_server()에서만
  # auth = build_auth_provider() 결과를 FastMCP(..., auth=auth)로 조건부 전달한다.
  ```
- **구현 내용**:
  1. `server/auth.py`에 `fastmcp.server.auth.providers`(구체 provider는 아직 결정
     안 됐으므로 자체 JWT 발급 방식인 `InMemoryOAuthProvider` 또는 `JWTIssuer` 기반
     최소 구성으로 시작 — 외부 IdP 연동은 이번 범위 아님, consent 화면과 토큰
     발급만 우리가 호스팅하는 최소 골격)로 인증 프로바이더를 구성하는 함수를 만든다.
  2. `OAUTH_ENABLED` 환경변수가 `"1"`이 아니면 `None`을 반환해 기존 배포 동작에
     영향이 없게 한다(기본값 비활성 — 필수).
  3. Redirect URI 두 종류(`https://tools.kakao.com/api/v1/applied-mcps/{mcpId}/...`,
     `https://playmcp.kakao.com/api/v1/applied-mcps/{mcpId}/...`)는 `mcpId`가
     아직 배포 확정 전이라 환경변수(`OAUTH_MCP_ID`)로 주입받게 하고, 미설정 시
     `build_auth_provider()`가 `None`을 반환하도록 가드한다.
  4. 개인정보 제3자 제공 동의 consent 화면은 이 태스크에서 실제 UI까지는 만들지
     않는다 — `fastmcp`가 제공하는 기본 consent 핸들러를 그대로 사용하고, 동의문
     문구(카카오 첨부2 양식: 제공받는자/제공목적/제공항목/보유기간)는 별도 문서로만
     `server/auth.py` 상단 주석에 초안을 남긴다(실제 동의문 제출은 사람이 함).
  5. `create_server()`에서 `auth = build_auth_provider()`를 호출해 `None`이면
     기존과 동일하게 `FastMCP(name=..., lifespan=...)`, 아니면
     `FastMCP(name=..., lifespan=..., auth=auth)`로 조립한다.
- **검증 명령**:
  ```
  pytest tests/test_auth.py -q
  OAUTH_ENABLED=1 OAUTH_MCP_ID=test python -c "from server.auth import build_auth_provider; print(build_auth_provider())"
  pytest -q
  ```
- **완료 조건**: `OAUTH_ENABLED` 미설정 시 `build_auth_provider() is None`,
  설정 시 유효한 프로바이더 인스턴스 반환. 전체 회귀 테스트(`pytest -q`) 통과 —
  즉 OAuth 코드가 있어도 지금 배포 동작이 전혀 바뀌지 않아야 한다.
- **금지 사항**:
  - `OAUTH_ENABLED` 기본값을 활성(`"1"`)으로 두지 말 것 — 반드시 명시적 opt-in
  - 실제 카카오 Redirect URI로 하드코딩하지 말 것(환경변수화 필수, mcpId 미정 상태)
  - 개인정보 제3자 제공 동의문의 실제 제출·디스코드 전송은 이 태스크 범위 밖
    (코드로 자동화하지 말 것 — 사람이 검토 후 수동 제출)
  - `contracts/schemas.py` 수정 금지

**[검증 결과 — 재작업 필요]** TASK-005 1차 구현은 `InMemoryOAuthProvider`를 그대로
사용했는데, 이 클래스는 FastMCP 공식 docstring상 "테스트용 인메모리 시뮬레이터"이며
`register_client()`가 클라이언트가 보낸 임의의 `redirect_uris`를 검증 없이 그대로
저장한다(`kakao_redirect_uris`라는 속성을 코드에 붙였지만 provider 내부 어디에서도
참조되지 않는 장식용 데이터). 즉 `OAUTH_ENABLED=1`을 켜도 카카오가 등록한 두 URI만
허용하는 실제 검증이 동작하지 않는다 — "승인 나면 스위치만 켜면 된다"는 전제가 깨짐.
아래 TASK-005b로 재작업한다.

---

## TASK-005b: OAuth provider를 Redirect URI 검증 가능한 구현으로 교체

- **depends_on**: TASK-005(1차 구현 완료 상태, 이미 병합됨)
- **배경**: TASK-005가 만든 `server/auth.py`의 `build_auth_provider()`가
  `InMemoryOAuthProvider`(테스트용, Redirect URI 미검증)를 반환하고 있다.
  실제로 승인 후 스위치를 켰을 때 동작하려면 카카오가 문서(`Kakao Tools 개발
  가이드.pdf` 6장 1-a)에서 요구하는 두 Redirect URI만 등록을 허용하는 provider가
  필요하다. `InMemoryOAuthProvider`를 상속해 `register_client()`만 오버라이드하고,
  토큰/클라이언트 저장을 프로세스 재시작에도 살아남도록 파일 기반으로 바꾼다
  (완전히 새 인증서버를 처음부터 작성하지 않는다 — 상속으로 최소 변경).
- **수정 대상 파일**:
  - `server/auth.py` (수정)
  - `tests/test_auth.py` (수정 — 케이스 추가, 기존 3케이스는 동작 유지되도록 필요시
    최소 조정 허용— 단 "OAUTH_ENABLED 미설정 시 None" 등 기존 검증 취지는 유지)
- **인터페이스 확정**:
  ```python
  # server/auth.py
  class KakaoRestrictedOAuthProvider(InMemoryOAuthProvider):
      """InMemoryOAuthProvider를 상속. register_client()에서 카카오가 사전 등록한
      Redirect URI 화이트리스트만 허용하도록 검증을 추가한다."""

      def __init__(self, allowed_redirect_uris: tuple[str, ...], **kwargs) -> None: ...

      async def register_client(self, client_info: OAuthClientInformationFull) -> None:
          """client_info.redirect_uris의 모든 URI가 allowed_redirect_uris 안에 있어야
          등록을 허용한다. 하나라도 화이트리스트 밖이면 ValueError를 raise한다."""
          ...

  def build_auth_provider() -> "AuthProvider | None": ...  # 시그니처는 TASK-005와 동일 유지
  ```
- **구현 내용**:
  1. 재현 테스트 먼저(루트 규칙 14): `tests/test_auth.py`에 카카오 화이트리스트 밖의
     Redirect URI로 `register_client()`를 호출하면 `ValueError`가 발생하는 케이스,
     화이트리스트 안의 URI면 정상 등록되는 케이스를 추가한다. 수정 전에는
     (현재 구현이 검증을 안 하므로) 실패해야 한다.
  2. `KakaoRestrictedOAuthProvider`를 `InMemoryOAuthProvider` 상속으로 만들고,
     `register_client()`를 오버라이드해 `client_info.redirect_uris`의 각 URI가
     생성자에서 받은 `allowed_redirect_uris`(문자열 비교, 정확 일치)에 포함되는지
     검사한다. 하나라도 없으면 `ValueError(f"허용되지 않은 redirect_uri: {uri}")`를
     raise하고, 전부 포함되면 `super().register_client(client_info)`를 호출한다.
  3. `build_auth_provider()`는 `InMemoryOAuthProvider()` 대신
     `KakaoRestrictedOAuthProvider(allowed_redirect_uris=(...))`를 생성해 반환하도록
     바꾼다. `kakao_redirect_uris` 동적 속성 할당 코드는 제거한다(생성자 인자로
     대체됐으므로 더 이상 필요 없음).
  4. 토큰/클라이언트가 프로세스 재시작 시 사라지는 문제는 이번 태스크 범위 밖으로
     명시한다(주석으로 "영속화는 별도 태스크" 남기기) — OAuth는 어차피 카카오 승인
     전까지 비활성이라 지금 당장 영속화가 급하지 않다.
- **검증 명령**:
  ```
  pytest tests/test_auth.py -q
  pytest -q
  ```
- **완료 조건**: 화이트리스트 밖 Redirect URI 등록 시도가 실제로 거부됨이 테스트로
  증명된다. 전체 회귀 테스트 통과.
- **금지 사항**:
  - `InMemoryOAuthProvider`의 토큰 발급/authorize 로직 자체는 다시 구현하지 말 것
    (상속으로 재사용, `register_client`만 오버라이드)
  - `OAUTH_ENABLED` 기본값을 활성으로 바꾸지 말 것
  - `contracts/schemas.py` 수정 금지

---

## 이번 계획에 포함하지 않는 것

1. **OAuth 실제 활성화** — 카카오 개인정보보호팀 승인 이후 별도 진행(동의문 제출은
   TASK-005 완료 후 사람이 수행)
2. **nickname과 OAuth user_id 랭킹 데이터 병합** — 이번엔 별개 키로 취급, 병합 로직은
   OAuth 활성화가 확정된 이후 별도 계획
3. **위젯 기반 랭킹 표시**(ListView/Table 등) — 이번엔 마크다운 텍스트로만 표시.
   위젯화는 별도 계획(HANDOFF.md §7 위젯 조사 결과 참고)
4. **주간 리셋 요일/시각의 최종 확정** — `DEFAULT_RESET_WEEKDAY=월요일 00:00 KST`는
   임시값. 실제 값은 팀 확인 후 상수만 교체하면 됨

## 전체 검증 (모든 태스크 완료 후)

```
pytest -q                                     # 전체 통과
python -m server.main &                       # 기동
curl -s -X POST http://127.0.0.1:8000/mcp ...  # quiz(nickname="테스트") 수동 호출 확인
```

**롤백**: `git status`로 변경 파일 확인 후 필요 시 `git checkout -- <file>`로 개별 되돌림.
