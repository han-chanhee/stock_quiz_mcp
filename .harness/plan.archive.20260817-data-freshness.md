# 작업 계획

> Claude Code가 초안을 만들고 사람이 승인합니다. Codex는 이 파일의 태스크 단위로만 작업합니다.

## 목표

퀴즈가 38일 전이 아닌 **출제 호출 시점에 가까운 시세**로 나오고, 배치가 다시는 조용히 데이터를 날리지 않는다.
(TASK-001~004: 배치 정합성 확보 → TASK-005: 리프레셔 주기를 1분으로 단축해 "출제 시점 시세"의 정확도를 높인다.)

## 배경 (이 계획이 필요한 이유)

- `HANDOFF.md` 기재: 데이터가 38일 경과. KIS 토큰 발급 1분 1회 제한에 걸려 배치가
  전부 실패했는데도 **성공으로 보고하며 `sector_top100.json`/`reasons.json` 을 0건으로 덮어씀**.
- 원인 확인됨: `batch/daily.py` 의 `_build_top20`(L96-101), `_build_movers` 는 예외를
  `continue` 로 격리해 기존 파일을 지키지만, `_build_sector_pool`(L164) 과
  `_build_reasons`(L240) 는 **가드 없이 `_dump`/`write_text` 를 무조건 호출**한다.
  종목 스냅샷이 전건 실패하면 `pool == []`, `reasons == {}` 가 그대로 파일에 써진다.
- `QuizCache.stale` 은 **파일 부재만** 본다(`cache.py` L56). 파일이 있으면 내용이
  38일 전이어도 `stale=False` 라서 `/health` 가 정상이라고 답한다.
- 현재 리포는 코드가 `tmp-move/` 안에 있고 `pyproject.toml`/`Dockerfile` 이 없어
  **`pytest` 도 `docker build` 도 실행 불가**하다. 따라서 이관이 모든 작업의 선행 조건이다.

## 하드 룰과의 관계 (중요)

사용자 요구는 "실시간 주식 정보"지만, 루트 `CLAUDE.md` 성능 제약은
*요청 경로에서 외부 API 호출 금지, 예외 없음* 이고 PlayMCP 응답속도 게이트는 평균 100ms다.

**따라서 이 계획은 요청 경로를 건드리지 않는다.** "실시간"은 아래로 구현한다:

- 요청 경로: 지금과 동일하게 `QuizCache` 에서만 조립 (외부 호출 0회)
- 신선도: 배치가 실제로 성공하게 만들고, 낡으면 `/health` 가 즉시 드러내게 한다
- 장중 갱신 주기(현재 10:00/13:00/15:40 3회)는 **이번 계획에서 변경하지 않는다**.
  주기 단축은 KIS 호출량·게이트에 영향이 있어 별도 논의 후 결정한다.

## 공통 제약

- 건드리면 안 되는 경로: `contracts/` (읽기 전용 — 수정 필요 시 작업 중단하고 제안만)
- 추가 금지 의존성: 없음. `requirements.txt`/`pyproject.toml` 은 원본 그대로, 버전 변경 금지
- 요청 경로(`server/handlers.py`, `QuizCache` 조회 메서드)에 외부 API 호출 추가 금지
- 루트 규칙 14 준수: 버그 수정은 **재현 테스트 작성 → 수정** 순서
- 기존 테스트 수정·삭제·skip 금지
- 주석/독스트링/커밋 메시지는 한국어
- Windows에서 WSL 경로에 git 실행 시 필요:
  `git -c safe.directory='%(prefix)///wsl.localhost/Ubuntu/home/ch/projects/stock-quiz-mcp'`

---

## TASK-001: 리포지토리 이관 — tmp-move 평탄화 + 누락 파일 복원

- **depends_on**: none
- **수정 대상 파일**:
  - `.gitignore` (수정 — 현재 `.harness/logs/` 한 줄뿐)
  - `tmp-move/**` → 리포 루트로 이동
  - `pyproject.toml`, `requirements.txt`, `Dockerfile`, `.dockerignore`, `fly.toml` (신규 복사)
  - `.github/workflows/docker-publish.yml` (신규 복사)
  - `.env` (신규 복사 — 추적 금지)
- **복사 원본**: `C:\Users\82109\Downloads\stock-quiz-mcp\stock-quiz-mcp\`
  (조사 완료: 공통 파일 57개 MD5 전부 일치, `tmp-move` 에만 있는 파일 0개 → 충돌·유실 없음)
- **구현 내용**:
  1. 원본 `.gitignore` 를 루트에 복사하고 맨 아래에 `.harness/logs/` 를 추가한다.
     `batch/data/*.json` 은 **절대 ignore 대상에 넣지 않는다** (기동 시 전수 검증 대상이고
     `.dockerignore` 도 `!batch/data/*.json` 로 되살린다). 바깥 `Downloads\.gitignore` 에는
     이 줄이 있으나 그건 다른 파일이니 가져오지 말 것.
  2. `git rm -r --cached` 로 `__pycache__`/`*.pyc`/`.hypothesis` 추적 해제.
     현재 추적 175개 중 118개가 이 캐시 쓰레기다 → 57개로 줄어야 한다. 파일 자체는 삭제 안 함.
  3. `tmp-move/` 의 `contracts, clients, services, store, batch, server, tests` 디렉터리와
     `HANDOFF.md`, `deploy_vm.sh` 를 리포 루트로 이동한다.
  4. `tmp-move/CLAUDE.md` 는 루트 하네스용 `CLAUDE.md` 와 **이름이 충돌**한다.
     → 루트 하네스 파일을 유지하고 프로젝트 문서는 `PROJECT.md` 로 이름을 바꿔 이동한다.
     모듈별 `*/CLAUDE.md` 는 충돌이 없으므로 이름 그대로 이동.
  5. `tmp-move/.venv/`(Windows 바이너리), `.hypothesis/`, `__pycache__/`, 빈 쓰레기 디렉터리
     `tmp-move/{contracts,clients` 를 삭제하고 빈 `tmp-move/` 를 제거한다.
  6. 위 신규 파일 6종을 원본에서 **복사**한다. 내용은 한 글자도 바꾸지 않는다.
  7. `.env` 는 **1번 완료 후에만** 복사한다. 복사 직후
     `git status --short` 에 `.env` 가 나타나면 즉시 중단하고 1번을 재점검한다.
  8. 루트에서 venv 재생성 후 `pip install -r requirements.txt`.
- **검증 명령**:
  ```
  test ! -d tmp-move && ls pyproject.toml Dockerfile server/main.py batch/data/top20_kr.json
  git ls-files | grep -cE '__pycache__|\.pyc$|\.hypothesis'
  git ls-files --error-unmatch .env; test $? -ne 0
  pytest -q
  ```
- **완료 조건**: `tmp-move` 부재, 위 파일 전부 존재, 캐시 추적 수 `0`,
  `.env` 미추적, `pytest -q` 종료 코드 0.
  `pyproject.toml` 의 `[tool.pytest.ini_options]`(`asyncio_mode="auto"`, `pythonpath=["."]`)가
  설정을 공급하므로 `PYTHONPATH` 수동 지정 없이 통과해야 한다.
  HANDOFF.md 기준 기대값은 **55 passed**, 다르면 그 차이를 보고한다.
- **금지 사항**:
  - 원본 `Downloads\` 폴더를 이동·삭제하지 말 것 (롤백 원본이므로 보존)
  - 소스 코드 **내용**을 편집하지 말 것 — 이 태스크는 이동/복사만 한다
  - `.env` 를 커밋하거나 `git add -f` 하지 말 것
  - `git reset --hard`, `git clean -fdx`, `git push` 금지

---

## TASK-002: 배치 빈 산출물 덮어쓰기 방지 (데이터 소실 사고 재발 방지)

- **depends_on**: TASK-001
- **수정 대상 파일**:
  - `batch/daily.py` (수정)
  - `tests/test_batch.py` (수정 — 기존 테스트는 유지하고 케이스만 **추가**)
- **인터페이스 확정**:
  ```
  class EmptyOutputError(RuntimeError): ...

  def _dump(path: Path, models: list, allow_empty: bool = False) -> None
  ```
- **구현 내용**:
  1. 루트 규칙 14에 따라 **재현 테스트를 먼저 작성**한다. `tests/test_batch.py` 에
     추가할 케이스: 모든 `snapshot()` 호출이 예외를 던지는 mock 클라이언트로
     `DailyBatch.run()` 을 돌렸을 때, 기존 `sector_top100.json` / `reasons.json` 의
     내용이 **보존되는지** 검증. 수정 전에는 실패해야 한다.
  2. `_dump` 에 `allow_empty` 파라미터를 추가한다. `allow_empty=False`(기본)인데
     `models` 가 비어 있으면 파일에 쓰지 않고 `EmptyOutputError` 를 던진다.
  3. `_build_sector_pool`(L164): `_dump` 호출을 `try/except EmptyOutputError` 로 감싸고,
     비었으면 기존 파일을 그대로 두고 `print("[batch] sector_top100 산출 0건 — 기존 파일 유지")`
     후 기존 파일 내용을 읽어 반환한다. `_build_top20` 의 격리 방식(L96-101)과 동일한 정책.
  4. `_build_reasons`(L240): `reasons` 가 빈 dict면 `reasons.json` 을 쓰지 않고
     동일하게 로그만 남긴다. (원래 "근거 없으면 저장 안 함"이 정상 동작이므로
     **전건 실패와 정상적 0건을 구분**해야 한다 — 후보 종목 수가 0보다 큰데
     결과가 0건이면 실패로 간주한다.)
  5. `_update_aliases`(L221)도 동일 정책을 적용한다 — `all_snaps` 가 비면 쓰지 않는다.
  6. `run()` 이 끝날 때 한 건이라도 산출 실패가 있었으면 **0이 아닌 종료 코드**로
     끝나도록 `batch/__main__.py` 가 아니라 `run()` 이 실패 목록을 반환하게 하지 말고,
     `run()` 내부에서 실패가 있었으면 `EmptyOutputError` 를 최종적으로 raise 한다.
     "성공했다고 보고하는 것"이 이번 사고의 핵심이므로 반드시 실패로 드러나야 한다.
- **검증 명령**:
  ```
  pytest tests/test_batch.py -q
  ```
- **완료 조건**: 위 명령 종료 코드 0. 새 재현 테스트 포함 전체 통과
- **금지 사항**:
  - 기존 `tests/test_batch.py` 의 케이스를 수정·삭제하지 말 것 (추가만)
  - `contracts/schemas.py` 를 수정하지 말 것
  - 산출 JSON의 스키마·필드명을 바꾸지 말 것 (`QuizCache` 가 그대로 파싱해야 함)
  - 실패를 `print` 로만 남기고 정상 종료하지 말 것

---

## TASK-003: stale 판정에 데이터 경과일 반영

- **depends_on**: TASK-001
- **수정 대상 파일**:
  - `server/cache.py` (수정)
  - `tests/test_server.py` (수정 — 케이스 **추가**)
- **인터페이스 확정**:
  ```
  # QuizCache
  STALE_AFTER_HOURS: int = 36

  @property
  def stale(self) -> bool
  ```
- **구현 내용**:
  1. 재현 테스트 먼저: `batch/data` 픽스처의 `as_of` 를 3일 전으로 만든 뒤
     `QuizCache(...).load().stale` 이 `True` 인지 검증. 수정 전에는 실패해야 한다.
  2. `QuizCache.stale`(L149-151)을 수정한다. 기존 파일 부재 플래그(`self._stale`)는
     그대로 두고, **추가로** `self._data_as_of` 가 `None` 이거나 현재시각과의 차이가
     `STALE_AFTER_HOURS` 를 넘으면 `True` 를 반환한다.
  3. 기준값 36시간의 근거를 주석으로 남긴다: 배치는 매 영업일 18시 실행이므로
     정상 운영 시 최대 경과는 24시간+주말 여유. 금요일 18시 배치 → 월요일 오전까지
     오탐하지 않도록 주말은 별도 고려하지 않고 36시간으로 둔다.
     (주말 처리 정밀화는 이번 범위 아님)
  4. `_track_as_of` 가 tz-aware datetime을 다루므로 현재시각 비교도 반드시
     tz-aware(KST 또는 UTC)로 한다. naive/aware 혼용 비교는 `TypeError` 를 낸다.
- **검증 명령**:
  ```
  pytest tests/test_server.py -q
  ```
- **완료 조건**: 위 명령 종료 코드 0. 오래된 `as_of` 로 `stale=True` 가 나온다
- **금지 사항**:
  - `/health` 응답의 **키 이름·구조를 바꾸지 말 것** (`{status, stale, data_as_of}` 고정 —
    배포 헬스체크와 `Dockerfile` HEALTHCHECK가 의존)
  - 기동 중단 조건(규칙 13)을 stale 판정과 섞지 말 것 — stale은 경고이지 기동 실패가 아니다
  - `tests/test_server.py` 의 기존 케이스 수정·삭제 금지 (이 파일은 fastmcp를 import하지
    않는 구조이므로 그 전제를 깨지 말 것)

---

## TASK-004: 실제 배치 실행으로 당일 데이터 확보

- **depends_on**: TASK-002, TASK-003
- **수정 대상 파일**:
  - `batch/data/*.json` (재생성)
- **구현 내용**:
  1. 실행 전 현재 `batch/data/` 전체를 `_backup/data_<오늘날짜>/` 로 복사해 둔다.
     (이번 사고의 복구가 이 백업 덕분이었다)
  2. `.env` 의 KIS 자격증명으로 `python -m batch` 를 실행한다.
  3. **KIS 접근토큰 발급은 1분 1회 제한**이다. 실패 시 즉시 재시도하지 말고
     최소 60초 간격을 두고 최대 2회만 재시도한다. `clients/ratelimit.py` 의 기존
     레이트리밋 구현을 사용하고 새로 만들지 않는다.
  4. 실행 후 `sector_top100.json` 과 `reasons.json` 의 엔트리 수가 **0이 아닌지**
     반드시 확인한다. 0이면 TASK-002가 실패로 잡아냈어야 하므로 그것부터 보고한다.
  5. 산출 데이터의 `as_of` 가 오늘 날짜인지 확인한다.
- **검증 명령**:
  ```
  python -c "import json,pathlib; d=pathlib.Path('batch/data'); print({p.name: len(json.loads(p.read_text(encoding='utf-8'))) for p in sorted(d.glob('*.json'))})"
  pytest -q
  ```
- **완료 조건**: 모든 JSON의 엔트리 수가 0보다 크고, `sector_top100.json` 은 사고 이전
  수준(약 100건 내외), `reasons.json` 도 0건이 아니다. 전체 테스트 통과
- **금지 사항**:
  - 백업 없이 배치를 실행하지 말 것
  - 토큰 제한에 걸렸을 때 짧은 간격으로 반복 재시도하지 말 것 (계정 차단 위험)
  - 데이터가 안 나온다고 JSON을 손으로 편집하거나 값을 지어내지 말 것
  - 실 API 호출을 테스트 코드에 추가하지 말 것 (테스트는 mock 전용 유지)

---

## TASK-005: 장중 리프레셔 주기를 1분으로 단축 (top20 포함)

- **depends_on**: TASK-002, TASK-003
- **배경**: 사용자 요구는 "출제 호출 시점의 실 시세"다. 요청 경로에 KIS를 직접 넣는
  것은 루트 `CLAUDE.md` 성능 제약(요청 경로 외부 API 호출 금지, 예외 없음, p99
  3,000ms)과 정면 충돌하므로 채택하지 않는다. 대신 리프레셔 주기를
  현재 장중 3회(10:00/13:00/15:40)에서 **1분 간격**으로 단축해 "출제 시점과 가까운
  시세"에 근사한다. 요청 경로(`QuizCache` 조회)는 이번에도 건드리지 않는다.
- **수정 대상 파일**:
  - `server/main.py` (수정)
  - `server/cache.py` (수정)
  - `tests/test_server.py` (수정 — 케이스 추가)
- **인터페이스 확정**:
  ```
  # cache.py — QuizCache
  def update_top20(self, market: Market, snaps: list[StockSnapshot]) -> None

  # main.py
  _REFRESH_INTERVAL_SEC: int = 60
  _MARKET_OPEN = (9, 0)   # KST
  _MARKET_CLOSE = (15, 30)  # KST

  async def _refresh_today(cache: QuizCache, client: MarketClient) -> None
  # 기존 시그니처 유지. 내부에서 top_market_cap(KR/US)도 호출해
  # cache.update_top20(market, snaps)까지 수행하도록 본문만 확장한다.

  async def _refresher_loop(cache: QuizCache, client: MarketClient) -> None
  # 기존 "다음 목표 시각까지 sleep" 로직을 제거하고,
  # 장중(09:00~15:30 KST)에는 60초 간격, 장외에는 다음 장 시작까지 sleep으로 교체.
  ```
- **구현 내용**:
  1. 루트 규칙 14에 따라 재현 테스트 먼저: `tests/test_server.py`에 임의 시각을
     주입해 `_refresher_loop`가 장중에 60초 간격으로 갱신을 트리거하는지,
     장외에는 갱신하지 않는지 검증하는 케이스를 추가한다(시간은 주입 가능한
     `clock`/`now` 파라미터로 테스트 결정론 확보 — `quiz_bank.QuizBank`가 이미 쓰는
     rng/clock 주입 패턴을 그대로 따른다). 수정 전에는 실패해야 한다.
  2. `cache.py`에 `update_top20` 추가. `update_movers`(L143-147)와 대칭 형태로 작성.
  3. `main.py`의 `_refresh_today`가 `movers` 4종(KR/US × up/down) 갱신에 더해
     `client.top_market_cap(market, n)`을 KR/US 각각 호출해 `cache.update_top20`으로
     반영한다. 개별 시장/방향의 실패는 기존과 동일하게 `except: continue`로 격리해
     다른 항목 갱신을 막지 않는다(부분 실패가 전체를 죽이지 않음 — TASK-002와 동일 원칙).
  4. `_refresher_loop`를 60초 간격 루프로 변경하되, 매 tick마다 현재 KST 시각이
     장중(09:00~15:30)인지 확인하고 장외면 갱신을 건너뛰고 다음 tick까지만 sleep한다
     (장 마감 후 불필요한 KIS 호출 방지). 5분 폴링을 금지하던 기존 주석은
     "1분 간격, 장중 한정"으로 갱신한다.
  5. 리프레셔가 갱신하는 필드(top20/movers)가 부분적으로만 성공했을 때 신선도가
     필드별로 어긋날 수 있다는 점을 주석으로 남긴다(TASK-003의 `data_as_of`는
     전체 중 최신값만 추적하므로, 일부 필드만 갱신돼도 `data_as_of`가 최신으로
     보일 수 있음 — 이번 태스크에서 필드별 stale 추적까지는 하지 않는다. 범위 밖).
- **검증 명령**:
  ```
  pytest tests/test_server.py -q
  ```
- **완료 조건**: 위 명령 종료 코드 0. 장중 60초 간격 갱신, 장외 미갱신, top20도
  리프레셔 대상에 포함됨이 테스트로 확인된다.
- **금지 사항**:
  - 사용자 요청 경로(`handlers.py`, `QuizCache` 조회 메서드)에 외부 API 호출 추가 금지
  - 리프레셔 실패를 사용자 응답에 노출하지 말 것(기존과 동일 — 조용히 기존 캐시 유지)
  - KIS 일일/분당 호출 한도를 코드로 확인할 수 없으므로, 이 태스크 완료 후 실제
    KIS 계정 문서에서 한도 초과 여부를 별도 확인할 것(이 태스크의 검증 범위 밖)
  - `tests/test_server.py`의 기존 케이스 수정·삭제 금지

---

## 이번 계획에 포함하지 않는 것

성격이 다르거나 미지수가 많아 분리한다. 각각 별도 계획이 필요하다.

1. **랭킹 시스템 / OAuth** — 영속 저장이 필요한데 현재 룰이 Redis·DB를 금지한다.
   저장 수단을 먼저 정해야 설계가 나온다 (JSON append? 외부 스토리지? 룰 완화?)
2. **위젯** — 가능 범위·디자인이 미정. 조사 먼저 (HANDOFF.md에 선행 조사 기록 있음)
3. **차트 퀴즈** — 차트 이미지 확보·렌더링 방안 미정
4. **테스트 베드** (부하·엣지케이스·타 MCP 충돌) — 위 기능 확정 후
5. **문서 드리프트 정리** — `server/CLAUDE.md`, `tests/GATES.md` 가 아직 "툴 5개"라고
   기술하나 실제는 `quiz`, `submit_answer` 2개

## 전체 검증 (모든 태스크 완료 후)

```
pytest -q                                     # 전체 통과
python -m server.main &                       # 기동 (데이터 검증 통과해야 뜸)
curl -s http://127.0.0.1:8000/health          # stale=false, data_as_of=오늘
docker build -t stock-quiz-mcp .              # 이미지 빌드 성공
```

**롤백**: 원본 `C:\Users\82109\Downloads\stock-quiz-mcp\stock-quiz-mcp\` 를 손대지 않으므로,
문제 시 `git reset --hard 890c3f7` 후 원본에서 다시 시작할 수 있다.
