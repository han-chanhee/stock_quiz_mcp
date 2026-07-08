# Fly.io 배포 가이드 (내가 직접 하는 순서)

목표: `https://<앱이름>.fly.dev/mcp` 를 얻어 PlayMCP에 등록.
예상 비용: 최소 구성 월 $2~4. 아래 4단계에서 **지출 상한**을 꼭 걸 것.

---

## 0. 준비물
- 신용카드 (Fly 가입 시 필요)
- 이 프로젝트 폴더 (Dockerfile, fly.toml 이미 있음)
- 로컬 `.env` 의 키 4개 (KIS 2개 + Naver 2개)

## 1. flyctl 설치 (Windows PowerShell)
```powershell
iwr https://fly.io/install.ps1 -useb | iex
```
설치 후 새 터미널을 열고 확인:
```powershell
fly version
```

## 2. 가입 / 로그인
```powershell
fly auth signup      # 처음이면 (브라우저 열림, 카드 등록)
# 이미 계정 있으면:  fly auth login
```

## 3. 비용 관리 (Fly엔 하드 상한/알림이 없음 — 주의)
Fly.io는 **하드 지출 상한도, 예산 알림도 제공하지 않습니다**(공식 문서 확인).
초과분은 그냥 청구됩니다. 대신 이 앱은 **구성 자체로 비용이 묶여** 있습니다:
- `fly.toml`이 **머신 1개(min=max=1) + 오토스케일 없음 + 512MB 고정** → 월 $3~4에서 사실상 고정.
- 비용이 늘 유일한 길(머신 증설/큰 VM)이 우리 설정엔 없음. 트래픽도 소량이라 대역폭 ≈ 0.

**할 일**: 배포 후 가끔 Fly 대시보드에서 **"month-to-date bill"** 만 눈으로 확인.
정말 "절대 초과 불가"가 필요하면 Fly(종량제) 대신 **정액제(AWS Lightsail $3.5/월 등)** 를 쓰세요.

## 4. 앱 만들기
`fly.toml`의 `app = "stock-quiz-mcp"` 이름은 **전역 고유**여야 합니다.
아래로 만들되, 이미 쓰인 이름이면 다른 이름으로:
```powershell
# 프로젝트 폴더에서
fly apps create stock-quiz-mcp
```
`Name has already been taken` 이 뜨면 `stock-quiz-mcp-hch` 처럼 바꾸고,
`fly.toml`의 `app = ` 값도 **동일하게** 수정하세요.

## 5. 키(시크릿) 주입 — 이미지에 안 굽고 런타임 주입
로컬 `.env`의 실제 값으로 바꿔 넣기:
```powershell
fly secrets set `
  KIS_APP_KEY="여기붙여넣기" `
  KIS_APP_SECRET="여기붙여넣기" `
  NAVER_CLIENT_ID="여기붙여넣기" `
  NAVER_CLIENT_SECRET="여기붙여넣기"
```
> 키는 batch/리프레셔용입니다. 없어도 서버는 baked 데이터로 뜨지만, 넣어야 갱신됩니다.

## 6. 배포
```powershell
fly deploy
```
빌드→업로드→기동까지 몇 분. 끝나면 URL이 출력됩니다.

## 7. 동작 확인
```powershell
fly status                     # State: started, 머신 1개인지 확인
fly logs                       # "Application startup complete" 보이면 OK
```
헬스체크 (브라우저나 curl):
```
https://<앱이름>.fly.dev/health   →  {"status":"ok","stale":false,...}
```
머신이 반드시 **1개**여야 함(인메모리 store 전제). 2개 이상이면:
```powershell
fly scale count 1
```

## 8. PlayMCP 등록
등록 폼에 아래 값 입력:

| 필드 | 값 |
|---|---|
| MCP 이름 | `주식사전 퀴즈` |
| MCP 식별자 | `stockquiz` |
| 인증 방식 | 인증 사용하지 않음 |
| **MCP Endpoint** | `https://<앱이름>.fly.dev/mcp` |

MCP 설명(≤500):
```
코스피/코스닥 종목으로 즐기는 주식 퀴즈입니다. 세 가지 모드가 있어요. ① 주가 퀴즈: 종목의 현재가 맞히기(±3% 정답). ② 시장 퀴즈: 기간 내 많이 오르거나 떨어진 종목 맞히기. ③ 종목 퀴즈: 섹터·현재가·시총순위 힌트로 회사 맞히기. 오답이면 힌트, 정답이면 현재가·시총순위·팩트 기반 미니분석과 함께 다음 퀴즈를 안내합니다. 단체방에서 함께 풀 수 있으며, 투자 권유가 아닌 정보/퀴즈 제공용입니다. 데이터는 정해진 시점의 스냅샷 기준입니다.
```
대화 예시 3개 (세 모드에 1:1):
```
주가 맞히기 퀴즈 ㄱㄱ
오늘의 시장 흐름 퀴즈
무슨 종목일까?
```
→ **"정보 불러오기"** 누르면 툴 2개(quiz, submit_answer)가 잡혀야 정상.

## 9. 문제 해결
- **"정보 불러오기" 실패** → `DISABLE_HOST_PROTECTION=1` 이 적용됐는지 확인:
  `fly deploy` 후에도 안 되면 `fly secrets set DISABLE_HOST_PROTECTION=1` 후 재배포.
- **502/시작 실패** → `fly logs` 확인. 대개 batch/data 검증 실패거나 포트 불일치.
  `internal_port=8000` 과 서버 `PORT=8000` 이 맞는지 확인(기본값 그대로면 OK).
- **머신이 자꾸 꺼짐** → `fly.toml`의 `auto_stop_machines=false`, `min_machines_running=1` 확인.

## 10. 운영 (배포 후)
- **데이터 신선도**: 이미지엔 배포 시점 스냅샷이 들어있음. 재배포하면 그 시점으로 갱신됨.
  매일 자동 갱신을 원하면 별도 설정 필요(요청 시 서버 내장 스케줄러로 만들어 드림).
- **업타임 모니터**(대회 권장): UptimeRobot(무료)에 `https://<앱이름>.fly.dev/health` 등록.
- **재배포**: 코드/데이터 바꾼 뒤 `fly deploy` 한 번.
- **끄기**(비용 정지): `fly scale count 0` (다시 켜기 `fly scale count 1`).

---
요약: 4~6(배포) → 7(확인) → 8(등록). 15분이면 끝납니다. (3은 비용 참고용)
