# 인계 문서 — 본선 추가 개발

**최종 갱신: 2026-08-15** (이전 버전은 8/7 작성)
다음 작업자용. **추측이 아니라 실측·검증한 것만** 적었다.
읽는 순서: 0(지금 상태) → 1(일정) → 2(다음 할 일) → 나머지 참조.

---

## 0. 지금 당장 알아야 할 것

### 배포는 끝났다
```
본선  https://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io/mcp   ✅ 200
예선  https://stock-quiz-mcp.playmcp-endpoint.kakaocloud.io/mcp              ✅ 200 (규정상 유지 중)
```
8/7에 본선 서버 생성 + 디스코드로 Endpoint URL 전달 **완료**. 툴 2개 정상 노출.
본선 서버 지연 실측 ~150~245ms(네트워크 포함). 예선(94ms)보다 높은데 원인 미확인 —
파드 사양 차이 또는 콜드 스타트 추정. **개발 끝나고 재측정 필요.**

### 코드는 7/8 이후 변경 없음
마지막 커밋은 `183d6ef`(7/8) 하나뿐. 8/15 기준 미커밋 변경:
```
 M .dockerignore    .hypothesis/ 추가
 M .gitignore       .hypothesis/ 추가
 ?? HANDOFF.md      이 파일
 ?? 본선_8월7일_할일.md   8/7 배포 절차 (완료됨, 참고용)
```

### 데이터는 여전히 7/8자 (38일 경과)
8/15에 배치 갱신을 시도했다가 **실패하고 롤백했다.** 아래 "배치 실패 사고" 참조.
현재 `batch/data/*.json` 16개 전부 무결(빈 파일 0개), 테스트 55개 통과.

---

## 1. 일정 — 8일 남았다

| 날짜 | 내용 | 상태 |
|---|---|---|
| 8/7 (금) | 본선 서버 생성 + Endpoint URL 디스코드 전달 | ✅ 완료 |
| **8/23 (일)** | **본선용 개발 완료.** 이후 오류 수정만 가능 | ⬅ 남은 기간 8일 |
| 8/24~26 | 카카오 QA. 오류 전달 시 빠른 수정 필요 | |
| 8/27 (목) | **코드 프리징** — 서버 수정 불가 | |
| 8/31 14:00 ~ 9/28 | 툴 챌린지 본선. 사용자 투표 + 심사위원 → 최종 10팀 | |

⚠️ **예선 서버는 삭제·중지 금지.** 계속 운영 + PlayMCP 공개 유지가 규정.

**변경 가능 범위**: [불가] 서비스 컨셉·서비스명 변경, Key/Token 인증(인증은 OAuth만).
[필수] 개발 가이드 준수, Kakao Tools Preview에서 정상 동작 확인.
[선택] 툴 고도화/추가/삭제, OAuth, 위젯.
예선과 **전혀 다른 컨셉/기능**이면 등록 거절 가능.

출처: 상위 폴더의 `[AGENTIC PLAYER 10] 공모전 본선 가이드 (1).pdf`, `Kakao Tools 개발 가이드.pdf`

---

## 2. 다음 할 일 (사용자가 4개 다 하겠다고 함)

사용자 계획: 랭킹 시스템 / 위젯 / 차트 퀴즈 / 테스트베드 (+ codex 코딩 · claude 관장 하네스).
**8일 기준 권장 순서 — 의존성이 순서를 강제한다:**

1. **데이터 갱신** — 다른 모든 것의 전제. 아래 사고 노트 읽고 진행할 것
2. **위젯 1개(퀴즈 출제)** — 최대 미지수. Preview에서 실제 렌더링 확인이 선행돼야
   랭킹 UI·차트 표현을 확정할 수 있다
3. **랭킹** — 위젯 확인 후
4. **차트 퀴즈** — 마지막. 안 되면 이것만 버리고 나머지 3개는 살린다

> 위젯 스펙 조사 **완료**(8/15). 결과는 아래 §7 참조. 재조사 불필요 —
> 컴포넌트 카탈로그를 SDK 소스에서 직접 확보했다.

### 미리 확정된 제약
- **런타임 실시간 시세 불가.** 지금 응답이 빠른 유일한 이유가 요청 경로에 외부 호출이 0이라서다.
  출제 시점 KIS 호출은 p99를 KIS에 종속시킨다. 대안은 배치 주기 상향.
- **런타임 이미지 생성 불가.** 차트는 배치에서 사전 렌더링하거나 위젯 네이티브 컴포넌트를 써야 한다.
- **OAuth는 8일 안에 무리.** 인증서버 자체 구축(카카오 미지원) + 개인정보 제3자 제공 동의문 +
  카카오 개인정보보호팀 검토. 랭킹 유저 식별은 **닉네임 파라미터**로 갈 것.
- ❓ **카카오가 요청에 유저 식별 헤더를 주는지 미확인.** FastMCP `Context` 주입하면 헤더를 볼 수 있다.
  Preview에서 실측하거나 디스코드로 문의할 것(인프라 문의라 답변 대상).

---

## 3. ⚠️ 배치 실패 사고 (2026-08-15) — 반드시 읽을 것

### 무슨 일이 있었나
KIS 연결 확인차 `top_market_cap()`을 한 번 호출한 직후 배치를 돌렸다.
**KIS 토큰 발급은 분당 1회 제한**이라 배치가 새 토큰을 못 받고 전 종목 조회가 403으로 실패했다.

### 피해와 복구
`_build_sector_pool()`과 `_build_reasons()`는 **조회가 전부 실패해도 무조건 파일을 쓴다.**
→ `sector_top100.json`, `reasons.json`이 **0건으로 덮어써짐** (종목 퀴즈가 죽는 상태).
사전 백업에서 즉시 복구, 테스트 55개 통과 확인. **현재 데이터는 무결하다.**

### 다시 돌릴 때 지켜야 할 것
1. **KIS를 건드리고 나서 최소 1분 대기.** 연결 테스트와 배치를 연달아 돌리지 마라
2. **반드시 사전 백업.** `cp batch/data/*.json <백업경로>/`
3. 실행:
   ```bash
   # .env 로드 후 실행 (Windows에선 아래처럼 파이썬으로 로드)
   .venv\Scripts\python.exe -c "import os;from pathlib import Path;[os.environ.setdefault(*l.split('=',1)) for l in Path('.env').read_text(encoding='utf-8').splitlines() if '=' in l and not l.startswith('#')];import runpy;runpy.run_module('batch',run_name='__main__')"
   ```
4. **실행 후 반드시 검증** — "배치 완료"가 떠도 믿지 마라:
   ```bash
   .venv\Scripts\python.exe -c "import json,glob,os;[print(os.path.basename(p), len(json.load(open(p,encoding='utf-8')))) for p in sorted(glob.glob('batch/data/*.json'))]"
   ```
   `sector_top100.json`과 `reasons.json`이 **0건이면 실패**다. 백업에서 되돌려라.

### 고쳐야 할 실제 결함
배치가 **전 종목 조회 실패에도 "배치 완료"를 출력하고 빈 파일을 쓴다.**
크론 무인 실행 시 아무도 모르게 서비스가 죽는다. 최소한:
- 조회 성공 건수가 임계치 미만이면 **파일을 쓰지 말고 비정상 종료**
- `_build_reasons` / `_build_sector_pool`에 "결과가 비면 기존 파일 유지" 가드 추가

(루트 `CLAUDE.md` 규칙 14: 재현 테스트 작성 → 수정 순서 강제)

### 백업 위치
```
C:\Users\82109\Downloads\stock-quiz-mcp\_backup\data_20260815\   (16개, 검증 완료)
```
**레포 밖**에 둔 이유: 레포 안에 두면 git에 잡히고 Docker 이미지에도 baked 된다.
현재 `batch/data`와 바이트 단위로 일치함을 확인했다(빈 파일 0개).

> 이전에 임시 디렉터리(`%LOCALAPPDATA%\Temp\claude\...\scratchpad\`)에 뒀던
> 8/07 백업은 **이미 정리되어 소실됐다.** 임시 디렉터리에 백업을 두지 마라.

복구 명령:
```bash
cp ../_backup/data_20260815/*.json batch/data/
```

---

## 4. 알려진 결함

### 4-1. 데이터 38일 경과 — 최우선
`batch/data/*.json` 전부 `2026-07-08T03:52` 기준. `reasons.json`은 4월자.
8/15 실측 삼성전자 **274,500원**인데 캐시는 7/8 가격. 괴리가 크다.
데이터가 이미지에 baked되므로 **갱신 → 커밋 → push → GHCR 재빌드 → KC 재배포** 순서.

### 4-2. stale 플래그가 나이를 안 본다
`server/cache.py`의 `_stale`은 `_read()`에서 **파일 부재로만** True가 된다. 데이터 나이는 판정에 없다.
그래서 38일 묵은 지금도 `/health`가 `stale:false`를 반환하고 응답 푸터에 `⚠️낡은 데이터`가 안 붙는다.
루트 `CLAUDE.md` 규칙 13("썩은 데이터로 조용히 서비스하는 것이 최악")과 정면 충돌.
`data_as_of`는 tz-aware(+09:00)이고 `None`일 수 있다. 규칙 14대로 재현 테스트 먼저.

### 4-3. 랭킹 도입 시 공정성 문제로 격상
묵은 가격은 지금은 촌스러운 정도지만, 점수가 걸리면 **실제 시세를 아는 사람이 오히려 틀린다.**
랭킹을 넣을 거면 데이터 갱신이 선택이 아니라 **전제조건**이다.

### 4-4. 기타
- `{contracts,clients/` — PowerShell brace expansion 실패로 생긴 빈 디렉터리. 삭제 무방
- `DEPLOY.md`가 "툴 5개"라 하는데 실제 2개(`quiz`, `submit_answer`). 문서 낡음
- `KAKAOCLOUD_DEPLOY.md` / `FLY_DEPLOY.md` / `deploy_vm.sh` / `fly.toml` — 예선 때 시도했다 버린 경로

---

## 5. 검증된 사실 (재조사 불필요)

### 인프라
```
이미지  ghcr.io/han-chanhee/stock-quiz-mcp:latest   (태그는 latest 하나뿐)
        amd64 digest sha256:d39ddd793d32694614e5a284b1bac3c5621213b6d2a07980448ea368bb26045a
        created 2026-07-08T02:25:41Z, linux/amd64, 익명 pull 가능(HTTP 200)
        baked ENV: HOST=0.0.0.0, PORT=8080, DISABLE_HOST_PROTECTION=1 / EXPOSE 8080
CI      main에 push → GitHub Actions가 linux/amd64 빌드 → :latest 덮어씀
```
⚠️ **예선 서버도 `:latest`를 쓴다.** main에 push하면 예선 파드 재시작 시 새 이미지를 끌어올 수 있다.
예선은 규정상 계속 살아 있어야 하므로, 본선 개발 중 이 점을 인지할 것.

⚠️ KC 컨테이너 포트는 **8080**이어야 한다(폼 기본값 8000 아님). 이미지가 8080을 listen한다.
업데이트는 KC **"재배포" 버튼** — 서버 삭제/재생성 아님. `Redeploying` → `Active` 대기(수 분).
PlayMCP 등록은 **"임시 등록"** (= "등록 및 심사 요청" 아님).

### 성능 실측
```
핸들러 CPU (in-process, Windows Python 3.12)
  quiz(주가)          21.7 us/op   46,050 ops/s
  quiz(시장)          27.1 us/op   36,939 ops/s
  quiz(종목)          31.5 us/op   31,708 ops/s
  submit_answer(오답) 53.9 us/op   18,556 ops/s
→ 퀴즈 경로는 병목 아님. 상한은 이 코드가 아니라 FastMCP/uvicorn이 정한다.

랭킹 읽기 방식별 (설계를 강제하는 수치)
  유저   1,000명 | 매번 전체정렬     188us | heapq top10    91us | 캐시 ~0.1us
  유저  10,000명 | 매번 전체정렬   3,383us | heapq top10   751us | 캐시 ~0.1us
  유저 100,000명 | 매번 전체정렬  58,409us | heapq top10 13,196us | 캐시 ~0.1us
```
**랭킹 설계 제약(필수)**: 점수 쓰기는 O(1)(`attempts`가 이미 store에 있음) /
랭킹은 읽을 때 계산 금지, 캐시된 top-N / top 10~20만 유지(가이드의 "result 크기 최소화"와도 일치) /
"내 순위"는 전체 정렬 없이 근사("상위 12%").
이유: ① 단일 이벤트 루프라 동기 정렬이 도는 동안 **모든 요청이 멈춘다**
② 인메모리 store 전제라 **replica를 못 늘린다** ③ p99 3,000ms 위반 시 카카오가 서비스 중단/삭제 가능.

### 배포 형상
- **단일 인스턴스 확정** — 같은 quiz_id로 12회 연속 제출 → attempts 1~12 단조증가, NOT_FOUND 0건
- 인메모리 store라 **재배포하면 진행 중 퀴즈·랭킹이 전부 소실**된다
- 랭킹 영속성 현실안: 인메모리 + 주기적 JSON 스냅샷. 본선 기간(8/31~9/28)은 재배포 금지라
  투표 기간은 통째로 커버됨. 외부 KV는 요청 경로에 네트워크 홉이 생겨 100ms 요건 위협
- 루트 `CLAUDE.md`의 "Redis 금지 / DB 금지"는 랭킹이 없을 때 쓴 규칙 → **개정 대상**

### KIS API (실측)
- **토큰 발급 분당 1회**(위반 시 403) — 이번 사고의 원인
- 시총랭킹 `FHPST01740000`: `fid_input_price_2` 필수
- `inquire-price`(FHKST01010100): 응답에 **종목명 없음** → ticker/큐레이션명 폴백
- 랭킹 **페이지당 30건**
- KIS 연결 자체는 8/15 정상 확인 (삼성전자 274,500원 / SK하이닉스 1,645,000원 조회 성공)
- 키 5개 전부 `.env`에 존재: `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT`,
  `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`

### 심사 대응
가이드 요구사항 대부분 이미 충족(툴명 규칙, annotations 5필드, description 영·국문 병기,
정제 마크다운, stateless, Streamable HTTP, protocolVersion 2025-03-26). 남은 것:
1. **Tool description 품질** — *"툴 호출이 잘되도록 Tool Description을 잘 정의하는 것도 심사 기준에 포함"*이라고 가이드에 **명시**. 가장 확실한 가점
2. **툴 개수 2개** — 권장 3~10개(20개 초과만 금지). 랭킹 툴 추가하면 해결
3. **위젯** — 권장이지 필수 아님. `widget`으로 감싸고, `status`는 쓰지 말고(카카오 자동 삽입),
   `copy_text` 포함. 버튼 `onClickAction`은 **URL 이동만** 지원(툴 재호출 불가).
   **PlayMCP AI채팅은 위젯 렌더링 미지원** → Kakao Tools Preview에서만 검증 가능
4. **US 모드** — `handlers.py`의 `US_ENABLED=False`인데 `inputSchema` enum엔 `US`가 남아 있다.
   ChatGPT가 US를 고르면 "준비 중" 안내만 나온다. enum에서 제거 검토

**Kakao Tools Preview**: `https://preview-chatgpt.kakao.com` (ChatGPT Plus/Pro 계정 필요,
Business/Enterprise 불가). 실제 ChatGPT 환경이라 **툴 호출 100% 보장 안 됨** —
"Kakao Tools 버튼"을 누른 채 발화하면 호출률 상승, `{툴이름} 툴을 사용해서 답변해줘`로 명시 호출 가능.

---

## 6. 개발 환경 함정

- 실행/테스트는 **`.venv\Scripts\python.exe`** (uv 설치 CPython 3.12). 시스템 Python은 3.9뿐이라 쓰면 안 됨
- uv 호출은 `py -3.9 -m uv ...`
- Windows 콘솔이 cp949라 한글 출력 시 **`PYTHONIOENCODING=utf-8`** 필요
- ⚠️ **git bash에서 curl `-d`에 한글을 직접 넣으면 cp949로 깨져 서버가 500을 뱉는다.**
  파일에 UTF-8로 쓴 뒤 `--data-binary @file` 또는 `\uXXXX` 이스케이프를 쓸 것.
  **서버 버그가 아니다** — 이걸로 한 번 헛짚었다
- `.env`를 shell `while read`로 파싱하면 마지막 줄을 놓칠 수 있다(개행 없을 때).
  파이썬으로 로드할 것
- 아키텍처 규칙은 루트 `CLAUDE.md`와 각 모듈 폴더 `CLAUDE.md`에. `contracts/`는 읽기 전용,
  모듈 간 직접 import 금지, 의존 방향 `server → services → (clients, store)`, 주석·독스트링은 한국어

---

## 7. 위젯 스펙 조사 결과 (8/15 완료)

**1차 출처**: `@openai/chatkit` npm 1.9.0의 `types/widgets.d.ts`(실제 렌더러 타입) +
`openai/chatkit-python` `chatkit/widgets.py` v1.6.5. 아래 프로퍼티명은 **소스에서 그대로 옮긴 것**(추측 없음).
오타 하나가 조용한 강등으로 이어지므로 그대로 쓸 것.

### ⚠️ 핵심 리스크: SDK 두 개가 서로 다르게 뒤처져 있다
- `Chart`는 **Python SDK에만 있고 렌더러 타입·공식 문서엔 없다**
- `Table`은 **렌더러에만 있고 Python SDK엔 없다**
- **카카오 렌더러가 어느 쪽인지 문서로 확인 불가** → 둘 다 "아마 됨"
- 스펙 위배 시 **조용히 일반 텍스트로 강등**된다(에러가 안 뜬다). 그래서 Preview 검증이 필수

### 루트 컨테이너 (최상위는 반드시 이 셋 중 하나)
| 타입 | 주요 프로퍼티 |
|---|---|
| `Card` | `children`, `background`, `border`, `size`(sm\|md\|lg\|full), `padding`, `collapsed`, `confirm`/`cancel`, `theme` |
| `ListView` | `children`(**ListViewItem[]만**), `limit`(number\|'auto'), `theme` |
| `Basic` | `children`, `direction`(row\|col), `gap`, `padding`, `align`, `justify`, `theme` |

⚠️ 카카오 규칙대로 **`status`는 두 루트 모두에서 제외**한다(카카오가 자동 삽입).

### 컴포넌트
| 타입 | 프로퍼티 (정확한 이름) |
|---|---|
| `Text` | `value`(필수), `size`(xs~xl), `weight`(normal\|medium\|semibold\|bold), `color`, `textAlign`(start\|center\|end), `italic`, `lineThrough`, `truncate`, `maxLines`, `minLines`, `width`, `streaming`, `editable` |
| `Title` | `value`(필수), `size`(sm\|md\|lg\|xl\|2xl~5xl), `color`/`weight`/`textAlign`/`truncate`/`maxLines` |
| `Caption` | `value`(필수), `size`(sm\|md\|lg) |
| `Markdown` | `value`(필수), `streaming` |
| `Badge` | `label`(필수), `color`(secondary\|success\|danger\|warning\|info\|discovery), `variant`(solid\|soft\|outline), `size`(sm\|md\|lg), `pill` |
| `Icon` | `name`(필수, 고정 목록), `color`, `size`(xs~3xl) |
| `Image` | `src`(필수, str), `alt`, `fit`(cover\|contain\|fill\|scale-down\|none), `position`, `frame`, `flush`, `radius`, `width`/`height`/`size`, `aspectRatio`, `margin`, `flex` |
| `Button` | `label`, `onClickAction`, `style`(primary\|secondary), `color`, `variant`(solid\|soft\|outline\|ghost), `size`(3xs~3xl), `iconStart`/`iconEnd`, `pill`, `block`, `disabled`, `submit` |
| `Divider` | `color`, `size`, `spacing`, `flush` |
| `Spacer` | `minSize` |
| `Box`/`Row`/`Col` | `children`, `align`(start\|center\|end\|baseline\|stretch), `justify`(start\|center\|end\|**between**\|around\|evenly\|stretch), `gap`, `padding`, `wrap`, `flex`, `border`, `background`, `radius`, `margin`. `Box`만 `direction` 추가 |
| `ListViewItem` | `children`, `onClickAction`, `gap`, `align` — **ListView 직속 자식 전용** |
| `Table` ⚠️ | `children`(Table.Row[]) / `Table.Row`: `children`(Table.Cell[]), `header` / `Table.Cell`: `children`, `align`/`vAlign`, `width`, `colSpan`, `rowSpan`, `colSize`, `padding` — **렌더러엔 있고 Python SDK엔 없음** |
| `Chart` ⚠️ | `data`(list[dict]), `series`(bar/area/line: `dataKey`/`label`/`color`/`stack`/`curveType`), `xAxis`, `showYAxis`, `showLegend`, `showTooltip`, `barGap`, `barCategoryGap` — **Python SDK엔 있고 렌더러·문서엔 없음** |

폼 계열(`Input`/`Select`/`Form` 등)도 존재하나 **카카오는 URL 이동만 지원하고 툴 재호출이 불가**하므로 쓸모없다.

유용한 `Icon.name`: `chart`, `analytics`, `star`, `star-filled`, `wreath`, `confetti`,
`check-circle-filled`, `circle-question`, `lightbulb`, `bolt`, `sparkle`, `play`, `reload`, `external-link`.

### 차트 표현 방안 — 판정
| 경로 | 판정 | 근거 |
|---|---|---|
| 네이티브 `Chart` | **아마 됨 (검증 필수)** | Python SDK에 완전한 스펙. 렌더러/문서엔 부재 → 미지원이면 조용히 텍스트 강등 |
| **유니코드 블록 스파크라인** `▁▂▃▄▅▆▇█` | **확실히 됨** | `Text.value`는 임의 문자열. 어떤 렌더러든 100% 동작, 런타임 비용 0 |
| 사전 생성 이미지 + CDN | 아마 됨 | `Image.src`는 str, 문서상 "백엔드가 호스팅" 명시. 카카오 도메인 제한 미확인 |
| SVG data URI 인라인 | **안 됨으로 간주** | 어떤 문서도 data URI 미언급. CSP/길이 제한 개연성 높음 |

**추천 (8일 기준)**: 유니코드 스파크라인을 **기본 경로로 확정**해 먼저 완성하고,
`Chart`는 Preview에서 확인되면 얹는 **선택적 업그레이드**로 둔다.
차트 퀴즈의 핵심은 "모양 구분"이라 8단계 블록 문자로 상승/하락/횡보/V자/역V자는 충분히 변별된다.
이미지 경로는 시간 남을 때의 3순위.

### 리더보드 위젯 JSON (검증된 컴포넌트만 사용)
`Table`이 불확실하므로 `ListView` + `Row` + `justify:"between"` 조합이 가장 안전하다.
```json
{
  "widget": {
    "type": "ListView",
    "children": [
      { "type": "ListViewItem", "align": "center", "children": [
        { "type": "Row", "align": "center", "gap": 8, "children": [
          { "type": "Badge", "label": "1", "color": "warning", "variant": "solid", "pill": true, "size": "sm" },
          { "type": "Text", "value": "투자의神", "weight": "semibold", "flex": 1, "truncate": true },
          { "type": "Text", "value": "1,250점", "weight": "bold", "textAlign": "end", "color": "success" }
        ]}
      ]}
    ]
  },
  "copy_text": "**주식대결 랭킹 TOP 10**\n\n1. **투자의神** — 1,250점",
  "name": "leaderboard"
}
```
핵심: 닉네임 `Text`에 **`flex:1`**을 줘야 점수가 오른쪽으로 밀린다. `truncate:true`로 긴 닉네임 붕괴 방지.
상위 3위만 `Badge` 색을 달리하면 시각적 위계가 산다.

### 퀴즈 출제 위젯 JSON
```json
{
  "widget": {
    "type": "Card", "size": "full", "padding": 16,
    "children": [
      { "type": "Row", "align": "center", "gap": 6, "children": [
        { "type": "Icon", "name": "circle-question", "color": "info", "size": "md" },
        { "type": "Badge", "label": "난이도 중", "color": "info", "variant": "soft", "size": "sm" }
      ]},
      { "type": "Spacer", "minSize": 8 },
      { "type": "Title", "value": "이 기업의 종목명은?", "size": "lg", "weight": "bold" },
      { "type": "Text", "value": "국내 시가총액 1위, 반도체 메모리 세계 선두", "size": "md", "maxLines": 3 },
      { "type": "Divider", "spacing": 12 },
      { "type": "Markdown", "value": "정답 제출용 ID: `QZ-8F3A21`" },
      { "type": "Caption", "value": "위 ID와 정답을 함께 말해주세요", "size": "sm" }
    ]
  },
  "copy_text": "**주식대결 퀴즈**\n\n이 기업의 종목명은?\n\n제출 ID: `QZ-8F3A21`",
  "name": "quiz_question"
}
```
⚠️ `Text.value` 안의 `\n`이 줄바꿈으로 렌더링되는지 **불확실**하다. 힌트는 항목별 `Text`를 `Col`에 담는 편이 안전.

### Preview에서 반드시 확인할 것 (우선순위 순)
각 항목은 **실패해도 조용히 텍스트로 강등**되므로 하나씩 단독 검증해야 원인 파악이 된다.
1. **`Chart` 렌더링 여부** — 되면 차트 퀴즈 설계가 완전히 달라진다. 가장 먼저
2. **`Table`/`Table.Row`/`Table.Cell`** — 되면 리더보드가 훨씬 깔끔해진다
3. **`Text.value` 내 `\n` 줄바꿈 처리** — 안 되면 `Col`+개별 `Text`로 분해
4. **`Image.src` 외부 URL 허용 여부 및 도메인 제한** — 이미지 경로의 생사
5. `Image.src`의 data URI 수용 여부 (기대치 낮게)
6. **`Markdown` 컴포넌트 지원 여부** — `copy_text`의 Markdown 지원과 **별개 문제**.
   미지원이면 `quiz_id` 강조를 `Badge`/`Title`로 교체
7. `Basic` 루트 지원 여부 — 카카오 문서 예시는 `ListView`/`Card`만 언급
8. **유니코드 블록 문자 폰트 렌더링·정렬** — 카카오톡 웹뷰 폰트에서 안 깨지는지
9. `ListView.limit` 동작 — TOP 10이 잘리는지
10. `Icon.name` 값들이 실제로 그려지는지 — 아이콘 세트 번들 여부 불확실

### 참고 URL
- https://developers.openai.com/api/docs/guides/chatkit-widgets — 공식 가이드 (Chart/Table 미기재)
- https://github.com/openai/chatkit-js/blob/main/packages/chatkit/types/widgets.d.ts — 렌더러 타입
- https://registry.npmjs.org/@openai/chatkit/-/chatkit-1.9.0.tgz — 퍼블리시 렌더러 타입 (Table 있음, Chart 없음)
- https://raw.githubusercontent.com/openai/chatkit-python/main/chatkit/widgets.py — Python SDK (**Chart 전체 스펙**, Table 없음)
- https://widgets.chatkit.studio/ — Widget Builder (SPA라 정적 페치 불가)

---

## 8. 이 세션(8/15)에서 실제로 바꾼 것

- `batch/data/sector_top100.json`, `reasons.json` — 배치 사고로 비었다가 **백업에서 복구**(원상복구, 순변화 0)
- `_backup/data_20260815/` 신규 — 임시 디렉터리에 있던 백업을 레포 밖 영구 위치로 이전
- `.dockerignore` / `.gitignore` — `.hypothesis/` 추가 (8/7 작업분, 미커밋)
- `HANDOFF.md` — 이 파일 갱신 (위젯 조사 결과 §7 추가, 배치 사고 §3 추가)
- **코드 변경 없음. 커밋·push 없음. 데이터 순변화 없음.**

### 파일 위치
```
C:\Users\82109\Downloads\stock-quiz-mcp\
  ├─ stock-quiz-mcp\          ← 실제 프로젝트(git 레포)
  │   ├─ HANDOFF.md           ← 이 문서
  │   └─ 본선_8월7일_할일.md    ← 8/7 배포 절차(완료됨, 참고용)
  ├─ _backup\data_20260815\   ← 데이터 백업 (레포 밖)
  └─ *.pdf                    ← 공모전 가이드 2종
```
