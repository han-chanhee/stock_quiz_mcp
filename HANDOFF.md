# 인계 문서 — 주식대결 MCP

**최종 갱신: 2026-08-26**
읽는 순서: 0(지금 상태) → 1(일하는 방식) → 2(시행착오) → 나머지 참조.

이전 버전(8/15 시점, 위젯 스펙 조사·배치 사고 기록)은 `HANDOFF.archive.20260815.md`에
남겼다. **위젯 컴포넌트 스펙 표는 아직 그 문서가 원본**이므로 위젯 작업 시 참고할 것.

---

## 0. 지금 당장 알아야 할 것

### 배포 상태

```
본선  https://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io/mcp
예선  https://stock-quiz-mcp.playmcp-endpoint.kakaocloud.io/mcp   (규정상 유지 중)
```

- **PlayMCP OAuth 안내 메일 기준 최종 mcpId** (2026-08-30): **87440044842919710**.
  Endpoint URL은 동일하게 유지됨(디스코드 제출 URL 안 깨짐).
- 툴 3개: `help`, `quiz`, `submit_answer`
- 테스트 112개 통과

### 현재 완료된 핵심 항목

1. **Git push 자동화** — `ops.release`가 WSL tracked tree를 Windows repo로 동기화,
   커밋, push, 빌드 대기, 원격 검증까지 처리한다.
2. **HN 하네스 모델 역할 변경** — 계획 `gpt-5.5`, 구현
   `gpt-5.3-codex-spark`, 검증 `claude`.
3. **OAuth 위젯/동의 화면 보강** — 모바일 레이아웃, deny redirect, HTML escaping까지
   테스트됨.
4. **OAuth 식별값 ↔ 랭킹 연결** — 툴 컨텍스트의 `subject`/`user_id`/`client_id`를
   점수 키로 우선 사용하고, 없으면 닉네임 fallback.
5. **매턴 실시간 랭킹 위젯** — 출제/오답/만료/이미 풂 응답 하단에 같은 주간 랭킹
   패널을 붙인다. 정답 응답은 득점 + TOP3 패널을 유지한다.
6. **차트형 시장 퀴즈 힌트** — 계약 enum을 건드리지 않고 기존 시장 퀴즈 위젯에
   스파크라인형 힌트를 추가했다.
7. **OAuth/점수 영속화** — OAuth 클라이언트/동의/토큰과 주간 점수 스냅샷을
   `store/data/` 아래 JSON으로 저장한다. `OAUTH_SNAPSHOT_PATH`로 OAuth 저장 경로 변경 가능.
8. **로컬 테스트베드** — `ops.testbed`로 위젯 payload 검증, 인프로세스 부하 스모크,
   MCP 툴명 충돌 체크 가능.

### 남은 항목 (우선순위 순)

1. **개인정보 제3자 제공 동의문 디스코드 미제출** — 현재 세션에는 디스코드 전송
   커넥터가 없어 자동 제출 불가. `CONSENT_SUBMISSION.md` 본문은 최신 구현/검증 상태로 갱신됨.
2. **운영 화면 재배포 수동 버튼** — URL 오픈은 `ops.release open-redeploy`로 가능하지만,
   콘솔 버튼 클릭 자동화는 브라우저 로그인/PIN/사용자 조작과 충돌할 수 있다.

---

## 1. 일하는 방식 (이게 제일 중요하다)

### 1-1. 배포 경로가 특이하다 — 반드시 이 순서

WSL(`~/projects/stock-quiz-mcp`)에서 개발하지만, Windows Git Credential Manager에
로그인이 들어 있으므로 push는 로컬 운영 CLI가 Windows 경로를 경유해 처리한다.

```
WSL에서 코드 수정 → 테스트
   ↓
python -m ops.release push -m "커밋 메시지"
   ↓
python -m ops.release wait-build
   ↓
PlayMCP in KC 콘솔에서 "재배포" 버튼
   ↓
python -m ops.release verify-remote
```

**왜 이렇게 하나**: WSL에는 GitHub 인증 정보와 원격 `origin`이 없다. Windows 쪽은
Git Credential Manager에 이미 로그인돼 있어 토큰 없이 push된다. WSL과 Windows의 git
히스토리는 서로 다른 계보라 merge/pull 대신 tracked file tree를 동기화한다.
`ops.release`는 `.env*` 같은 민감 파일이 tracked 상태면 중단한다.

수동 동기화가 필요하면 예전 방식도 가능하지만, 기본은 아래 명령 하나다.
```bash
python -m ops.release push -m "release: oauth and ops automation"
```

### 1-2. 재배포 타이밍 함정 — 두 번 당했다

**GitHub Actions 빌드가 끝나기 전에 재배포 버튼을 누르면 옛 이미지를 끌어온다.**
겉으로는 "재배포 성공"으로 보여서 원인 파악에 시간을 날린다.

반드시:
1. GitHub Actions 탭에서 **초록불 확인** (또는 `gh run list`)
2. 그 다음 재배포
3. 재배포 후 curl로 실제 반영 확인 (아래 1-3)

### 1-3. "배포됐다"를 믿지 말고 curl로 확인해라

재배포 후 매번 확인한 명령들:
```bash
B=https://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io

curl -s $B/health          # data_as_of가 최신인지
curl -s -X POST $B/mcp -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```
`/health`의 `data_as_of`만 보면 안 된다 — 그건 배치 데이터 시각이라 코드
변경과 무관하게 그대로일 수 있다. **코드 반영은 실제 툴을 호출해서 응답
내용으로 확인**해야 한다(예: 새로 만든 `help` 툴이 뜨는지).

### 1-4. 재배포하면 인메모리 상태가 전부 날아간다

진행 중이던 quiz_id가 무효화되고("존재하지 않는 quiz_id"), 랭킹 점수도 초기화된다.
테스트 중 갑자기 quiz_id가 안 먹으면 재배포 때문일 가능성이 크다.

### 1-5. Preview 테스트는 LLM 의존이라 재현이 안 될 수 있다

카카오 가이드 원문: *"MCP 툴 호출은 전적으로 ChatGPT LLM에 의존"*, *"툴 호출이
100% 보장되지 않는다"*.

실제로 겪은 것: `quiz`는 잘 호출되는데 **`submit_answer`만 LLM이 우회하고
자기가 답변을 지어내는 현상**. `"stockquiztest-submit_answer 툴을 사용해서
답해줘"`처럼 툴 이름을 명시해도 안 될 때가 있었다.

식별 방법: **응답 문구가 우리 서버 코드에 없는 말이면 툴이 호출 안 된 것이다.**
예) "다시 도전해봐!", "첫 번째 채점이 완료됐습니다" ← 우리 코드에 없는 문구.
우리 서버는 항상 `"❌ 오답입니다. (시도 N회)\n\n💡 힌트: **{hint}**"` 형식.

Preview에서 툴 호출률 올리는 법(가이드 기준):
- "Kakao Tools 버튼"을 누른 채 발화
- `{MCP식별자}-{툴이름} 툴을 사용해서 답변해줘`로 명시 호출
- 위젯 확인 후에는 **"새 대화 시작"**으로 초기화 (이전 대화가 오염되면 계속 재현됨)

### 1-6. MCP 식별자와 툴 이름

Preview에서 LLM에게 전달되는 툴 이름은 `{MCP식별자}-{툴이름}` 형태다.
- MCP 식별자는 PlayMCP 등록 폼의 "MCP 식별자" 필드값 (영문·숫자만)
- 테스트용으로 `stockquiztest` 식별자로 임시 등록해서 쓰기도 했다
  → 실제 툴 이름: `stockquiztest-quiz`, `stockquiztest-submit_answer`

### 1-7. 로컬 테스트베드

배포 전 빠른 확인:
```bash
.venv/bin/python -m ops.testbed widgets
.venv/bin/python -m ops.testbed load --requests 200 --concurrency 20
.venv/bin/python -m ops.testbed conflicts
.venv/bin/python -m ops.release oauth-smoke
.venv/bin/pytest -q
```

- `widgets`: 대표 payload 12개가 JSON 직렬화되고 Preview 위험 컴포넌트(`Table`,
  `status`)를 쓰지 않는지 확인.
- `load`: 캐시/스토어/핸들러를 인프로세스로 조립해 출제 + 오답 제출을 반복.
- `conflicts`: 등록 툴이 `help`, `quiz`, `submit_answer`뿐인지 확인.
- `oauth-smoke`: 원격 DCR → authorize → 동의 화면 → token → 인증된
  `tools/list` → 인증된 `quiz` 호출까지 확인.

---

## 2. 시행착오 기록 (같은 실수 반복 방지)

### 2-1. 위젯이 "조용히 텍스트로 강등"된다

**에러가 안 뜬다.** 스펙 위반이 있으면 카드 UI 대신 그냥 평문 텍스트로 나온다.
원인 파악이 어려우니 아래 순서로 좁혀라:

1. curl로 서버 응답 확인 → 위젯 JSON이 정상이면 서버는 문제없음
2. 그럼 렌더러(카카오)가 거부한 것 → 컴포넌트를 하나씩 빼보며 범인 찾기

**실측으로 확인된 것 (2026-08-19)**:
- ❌ **`Table` 컴포넌트 = 강등 원인**. HANDOFF 아카이브의 경고가 사실로 확인됨.
  (렌더러 타입에는 있는데 카카오가 지원 안 함)
- ✅ `Card`, `Col`, `Row`, `Text`, `Title`, `Badge`, `Icon`, `Divider`,
  `Spacer`, `Markdown`, `Caption`, `Button` — 정상 렌더링 확인
- `ListView`/`ListViewItem`은 **루트 전용·자식 전용**이라 `Card` 안에 중첩하지
  않았다(위험 회피). 리더보드는 `Col` + `Row` 조합으로 구현
  (`server/widgets.py`의 `leaderboard_listview_rows`)

### 2-2. 버튼으로 툴 재호출은 불가능하다 (확정)

`Button.onClickAction`은 **URL 이동만** 지원한다(카카오 가이드 3장 명시).
"버튼 누르면 자동으로 다음 퀴즈" 같은 UX는 이 플랫폼에서 구현 불가.

실측으로 시도해본 것들 (전부 실패 또는 검증 불가):
- `kakaotalk://`, `kakaolink://`, `sms:`, `intent://` 딥링크 스킴 → 검증 못 함
- ChatKit의 `handler:"client"` + `sendUserMessage` 방식 → 카카오 미지원 추정
  (ChatKit JS 클라이언트를 우리가 제어할 수 없음. 그 JS는 카카오 소유)

**결론**: 지금 `correct_answer_widget`의 "다음 퀴즈/다른 퀴즈/종료" 버튼은
눌러도 반응 없는 **장식용**이다. 이걸 진짜 동작하게 만들 방법은 없다.

### 2-3. OAuth — 가장 많이 헤맨 부분

#### 방향을 반대로 이해했다
카카오 담당자 지적: **주식대결(우리) → 카카오**로 개인정보를 제공하는 흐름이다.
우리가 카카오에서 정보를 받아오는 게 아니다. 동의문의 "제공 목적"은
**우리가 넘긴 식별값이 Kakao Tools 답변에 어떻게 쓰이는지**를 써야 한다.
→ 최종 문구: "주식대결 퀴즈 정답/오답 기록, 가점·감점 기반 점수 산정,
   주간 랭킹(TOP3 및 본인 순위) 조회 및 Kakao Tools 답변 노출"

#### Redirect URI 경로 오타 (중요)
```
✅ .../applied-mcps/{mcpId}/authorize/oauth:callback
❌ .../applied-mcps/{mcpId}/oauth/callback          ← 처음에 이렇게 썼다가 틀림
```
`authorize/oauth:callback`이다. 콜론(`:`)이 들어간다.

#### 배포가 죽은 이유 1: Issuer URL must be HTTPS
`InMemoryOAuthProvider`의 `base_url` 기본값이 `http://fastmcp.example.com`인데
MCP SDK가 HTTPS를 강제해서 **기동 시 ValueError로 컨테이너가 죽었다.**
→ `_DEFAULT_BASE_URL`을 실제 배포 도메인(HTTPS)으로 명시해 해결.

#### 401은 장애가 아니라 스펙 요구사항이다 (제일 중요)
MCP 인증 스펙(2025-03-26) 원문:
> When authorization is required and not yet proven by the client, servers
> **MUST** respond with *HTTP 401 Unauthorized*. Clients initiate the OAuth 2.1
> authorization flow after receiving the *HTTP 401 Unauthorized*.

`tools/list`가 401을 반환하는 걸 "서비스 장애"로 오판하고 auth를 떼어냈다가
되돌렸다. **401이 있어야 카카오가 OAuth 흐름을 시작하고 동의 화면을 띄운다.**

#### 진짜 원인: Dynamic Client Registration 미활성
`/register` 엔드포인트가 없어서 클라이언트가 `client_id`를 얻을 방법이 없었다.
401을 받아도 다음 단계로 못 넘어가 흐름이 시작조차 안 됐다.
→ `ClientRegistrationOptions(enabled=True)`로 해결.

#### 환경변수를 배포 후 수정할 수 없다
PlayMCP in KC 콘솔의 서버 상세 페이지는 **읽기 전용**이다. "환경변수가 없습니다"만
표시되고 편집 UI가 없다. 재배포 버튼을 눌러도 환경변수 입력 화면이 안 나온다.
→ **해결책**: `mcpId`를 코드 상수(`server/auth.py`의 `_HARDCODED_MCP_ID`)로
   하드코딩. 이제 `OAUTH_ENABLED=1` 환경변수 하나만 있으면 동작한다.
   (신규 등록 폼에는 환경변수 입력란이 있으므로 거기서 넣는다)

#### OAuth 전체 흐름 실측 완료 (2026-08-20, 로컬)
```
tools/list          → 401 (WWW-Authenticate 헤더 포함)
/.well-known/...    → 200 (메타데이터)
/register           → 201 (client_id, client_secret 발급)
/authorize          → 302 → /oauth/consent?token=... (동의 화면)
동의 allow          → 302 → 카카오 콜백 URL?code=...&state=...
/token              → 200 (access_token + refresh_token)
/oauth/disconnect   → 200 (연동 해제 화면)
```

### 2-4. 데이터 신선도 / 배치

- **배치가 빈 결과로 기존 파일을 덮어쓰던 버그** 수정 완료
  (`batch/daily.py`에 `EmptyOutputError` 가드)
- **US(`NotImplementedError`)를 실패로 오분류**해서 정상 배치가 실패 종료하던
  버그도 수정. `US_ENABLED=False`라 US 조회 실패는 **정상 skip**이다.
- `QuizCache.stale`이 파일 부재만 보던 문제 → **36시간 경과 판정** 추가
  (`STALE_AFTER_HOURS`)
- KIS 토큰 발급은 **분당 1회 제한**. 연결 테스트 직후 배치를 돌리면 403으로
  전부 실패한다. 최소 1분 간격을 둘 것.
- 배치 실행 전 **반드시 백업**: `cp batch/data/*.json ../_backup/data_$(date +%Y%m%d)/`

### 2-5. 테스트 환경 함정

- WSL 시스템 python은 3.14인데 `ensurepip`가 없어 `python3 -m venv`가 실패한다.
  → `python3 -m venv --without-pip .venv` 후 `get-pip.py`로 부트스트랩했다.
- `MOCK_AS_OF`가 고정 과거 시각이면 stale 판정에 걸려 무관한 테스트가 깨진다.
  → `clients/base.py`에서 "현재 시각 - 1시간"으로 계산하게 바꿨다.
- `tests/test_gate1_spec.py`는 `server/main.py` **소스를 정적 분석**한다.
  주석에 "kakao" 문자열이 들어가면 테스트가 깨진다(카카오 스펙: 툴명/설명에
  플랫폼명 금지). 주석 쓸 때 주의.

---

## 3. HN 하네스 사용법

`.harness/` 디렉터리 기반. **Claude Code가 계획·검증, Codex가 구현**하는 구조.

### 3-1. 기본 흐름

```
[사람] 요청
  ↓
[Codex 5.5] 코드 탐색 → .harness/plan.md 작성 (태스크 단위로 쪼갬)
  ↓
[사람] plan.md 검토 (유일한 개입 지점)
  ↓
[Codex Spark] 태스크별 구현 + 검증 명령 실행
  ↓
[Claude Code] 계획 대비 검증 → PASS/FAIL
```

### 3-2. 실제로 쓴 명령

현재 `~/bin/hn` 기본 모델:
- 계획: `HN_PLAN_MODEL` 기본값 `gpt-5.5`
- 구현: `HN_IMPL_MODEL` 기본값 `gpt-5.3-codex-spark`
- 검증: `HN_VERIFY_CMD` 기본값 `claude`

Codex Spark 직접 호출 예:
```bash
python3 -c "
import pathlib
tpl = pathlib.Path('.harness/prompts/implement.md').read_text()
plan = pathlib.Path('.harness/plan.md').read_text()
out = tpl.replace('{{TASK_ID}}', 'TASK-002').replace('{{PLAN}}', plan)
pathlib.Path('/tmp/impl_task002.md').write_text(out)
"

codex exec -m gpt-5.3-codex-spark -s workspace-write --skip-git-repo-check "$(cat /tmp/impl_task002.md)

참고: venv는 .venv/에 있고 테스트는 .venv/bin/pytest로 실행한다."
```

`--full-auto`는 이 버전(codex-cli 0.147.0)에 없다. `-s workspace-write` 사용.

### 3-3. 하네스 운영 노하우

**계획서 아카이브**: `hn`은 `.harness/plan.md` 고정 경로만 읽으므로, 새 사이클
시작 시 이전 계획을 `plan.archive.YYYYMMDD-주제.md`로 복사하고 덮어쓴다.
현재 3개 아카이브가 있다(data-freshness, ranking-oauth, widgets).

**병렬 실행**: 태스크가 서로 다른 파일을 건드리면 동시에 돌려도 안전하다.
같은 파일을 건드리면 순차로 해야 한다. 실제로 TASK-002(`batch/daily.py`)와
TASK-003(`server/cache.py`)를 병렬로 돌려 시간을 절반으로 줄였다.

**Codex가 계획을 넘어설 때가 있다**: TASK-002에서 계획에 없던
`_build_top20`/`_build_movers`까지 실패 추적을 확장했다. 이건 계획서 6번
조항("한 건이라도 실패면 raise")을 문자 그대로 만족시키려면 필연적이었고,
오히려 내가 검토 때 지적했던 계획의 갭을 메운 것이었다.
→ **검증 시 "계획 밖 변경"을 무조건 FAIL로 보지 말고, 계획 의도를 달성하는 데
   필요했는지 판단할 것.**

**Codex가 정직하게 막힐 때가 있다**: TASK-003에서 "기존 테스트 수정 금지" 규칙
때문에 진행을 멈추고 실패를 보고했다. 원인은 `MOCK_AS_OF` 고정값과 새 stale
정책의 충돌이었고, 계획 자체가 놓친 부분이었다.
→ **Codex가 멈추면 계획을 의심해라.**

**하네스를 안 쓴 경우도 있다**: 위젯 14개 화면 작업은 실시간 반복 검증이
필요해서 Claude Code가 직접 구현했다(사용자가 "알아서 해놔"라고 위임한 상황).
속도가 중요하고 검증 사이클이 짧으면 직접 하는 게 낫다.

---

## 4. 파일 지도

### 문서
| 파일 | 용도 |
|---|---|
| `HANDOFF.md` | 이 문서 |
| `HANDOFF.archive.20260815.md` | 이전 인계 문서 (**위젯 컴포넌트 스펙 표 원본**) |
| `PROJECT.md` | 아키텍처 규칙 (Redis 금지, 의존 방향 등) |
| `CLAUDE.md` | 하네스용 프로젝트 규칙 |
| `CONSENT_SUBMISSION.md` | 개인정보 동의문 + 디스코드 복붙용 텍스트 |
| `KAKAO_QUESTIONS.md` | 카카오 문의용 질문 목록 (환경변수 이슈) |
| `.harness/plan.md` | 현재 계획 |
| `.harness/plan.archive.*.md` | 완료된 계획 3개 |

### 코드 (모듈별 CLAUDE.md도 각 디렉터리에 있음)
| 경로 | 역할 |
|---|---|
| `contracts/schemas.py` | **읽기 전용 계약**. 수정 시 사용자 승인 필요 |
| `clients/` | KIS API 래퍼 + mock |
| `services/` | 퀴즈 출제·채점·힌트·미니분석 |
| `store/quiz_store.py` | quiz_id TTL 스토어 |
| `store/score_store.py` | 랭킹 (OAuth/플랫폼 식별자 우선, 닉네임 fallback, 주간 리셋, JSON 스냅샷) |
| `server/handlers.py` | 툴 오케스트레이션 (fastmcp 비의존) |
| `server/widgets.py` | 위젯 JSON 조립 (공통 랭킹 패널, 차트형 시장 힌트 포함) |
| `server/auth.py` | OAuth 인증서버 (동의/연동해제 화면 포함) |
| `server/main.py` | FastMCP 엔트리, 툴 3개 등록 |
| `batch/` | 일일 데이터 갱신 |
| `ops/release.py` | 로컬 push/build/원격 검증/화면 오픈 자동화 |
| `ops/testbed.py` | 로컬 위젯/부하/툴 충돌 테스트베드 |

---

## 5. 다음 사람이 할 일 (순서대로)

1. **동의문 디스코드 제출** — `CONSENT_SUBMISSION.md` 복붙
2. **승인 후 OAuth 켜기** — 신규 등록 폼에서 `OAUTH_ENABLED=1` 환경변수 추가
3. **심사 피드백 반영** — 개인정보보호팀이 문구/화면 수정을 요구하면 `server/auth.py`와
   `CONSENT_SUBMISSION.md`를 함께 수정

### 손대지 말 것
- 예선 서버(`stock-quiz-mcp`) 삭제·중지 — 규정 위반
- `contracts/schemas.py` 임의 수정
- 서버 재생성 시 Endpoint URL 확인 필수 (바뀌면 디스코드 재제출)
