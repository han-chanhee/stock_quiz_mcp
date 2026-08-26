# 개인정보 제3자 제공 동의문 — 디스코드 제출용

카카오 개발 가이드 6장 1-c에 따라, 아래 내용을 팀별 디스코드 채널에 전달해
카카오 개인정보보호팀 검토를 받는다. 현재 세션에는 디스코드 전송 커넥터가 없으므로,
제출 본문과 검증 결과를 최신 상태로 유지한다.

## 정보 흐름 방향 (2026-08-19 카카오 담당자 확인 반영)

**주식대결(우리 서비스) → 카카오**로 개인정보를 제공하는 흐름이다. 사용자가
OAuth로 로그인하면 우리가 보유하게 되는 이용자 식별값과, 그 값에 연결된
서비스 이용 정보(정답 기록·점수·랭킹)를 카카오에 제공해 Kakao Tools 답변에
노출시킨다. (이전 초안은 방향을 반대로 이해하고 있었음 — 카카오 담당자
피드백으로 수정)

## 카카오 담당자 질문에 대한 답변 (제출 시 함께 전달)

```
네, 이해가 맞습니다. 방향을 반대로 설명드렸던 것 같습니다.

주식대결 MCP는 카카오톡 대화 안에서 사용자를 구분해 주간 랭킹(점수·순위)을
제공하고 있습니다. OAuth 인증을 통해 카카오로부터 받은 이용자 식별값을
저희 랭킹 시스템의 식별 키로 활용하고, 그 결과(정답 여부, 획득 점수, 순위)를
Kakao Tools 답변에 노출하는 데 사용합니다.

제공 목적을 아래와 같이 구체화했습니다:
"주식대결 퀴즈 정답/오답 기록, 시도 횟수 기반 점수 산정, 주간 랭킹(TOP5 및
본인 순위) 조회 및 Kakao Tools 답변 노출"
```

## 제출 전 확인할 것

- [x] `mcpId` 확정: **3606** (stock-quiz-mcp-kakaotools 서버 재생성 후 새 ID —
      Endpoint URL은 동일하게 유지됨)
- [x] 제공 목적/항목을 실제 정보 흐름 방향(우리→카카오)에 맞게 재작성
      (2026-08-19, server/auth.py 반영 완료)
- [x] mcpId를 코드 상수로 하드코딩(`server/auth.py`의 `_HARDCODED_MCP_ID`)해
      `OAUTH_MCP_ID` 환경변수 없이도 `OAUTH_ENABLED=1`만으로 활성화 가능하게 함
      (콘솔에서 등록 후 환경변수 수정 방법을 못 찾은 문제 우회)
- [x] Redirect URI 2개 확정(아래 "함께 전달할 내용" 참고, mcpId=3606 반영됨)
- [x] OAuth 식별값을 랭킹 점수 키에 연결 완료
- [x] 동의/클라이언트/토큰 스냅샷 영속화 완료(`OAUTH_SNAPSHOT_PATH`로 경로 변경 가능)
- [x] 원격 OAuth 흐름 검증 완료: DCR, authorize, 동의 화면, token, 인증된 `tools/list`

---

## 디스코드에 복붙할 내용 (지금 바로 제출 가능)

```
[개인정보 제3자 제공 동의문 재검토 요청]

서비스: 주식대결 (stock-quiz-mcp-kakaotools, mcpId: 3606)

■ 개인정보 제3자 제공 동의문
- 제공받는자: (주) 카카오
- 제공목적: 주식대결 퀴즈 정답/오답 기록, 시도 횟수 기반 점수 산정, 주간
  랭킹(TOP5 및 본인 순위) 조회 및 Kakao Tools 답변 노출
- 제공항목: 이용자 식별값 및 그에 연결된 점수·랭킹 정보
- 제공받는 자의 보유 및 이용기간: 연동 해제 시 지체없이 파기

■ Redirect URI (2개 모두 등록 요청)
- https://tools.kakao.com/api/v1/applied-mcps/3606/authorize/oauth:callback
- https://playmcp.kakao.com/api/v1/applied-mcps/3606/authorize/oauth:callback

■ 연동 해제 화면
https://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io/oauth/disconnect

■ 동의 화면
구현 및 원격 검증 완료. 실 서비스 OAuth 흐름에서 발급되는 동의 토큰으로만
접근 가능한 구조이며, 토큰 없이 접근 시 400 응답을 반환합니다.

■ 추가 문의
OAuth 동의 화면 및 연동 해제 화면이 심사 기준에 맞는지 확인 부탁드립니다.
```

## 개인정보 제3자 제공 동의문

| 항목 | 내용 |
|---|---|
| **제공받는자** | **(주) 카카오** |
| **제공목적** | 주식대결 퀴즈 정답/오답 기록, 시도 횟수 기반 점수 산정, 주간 랭킹(TOP5 및 본인 순위) 조회 및 Kakao Tools 답변 노출 |
| **제공항목** | 이용자 식별값 및 그에 연결된 점수·랭킹 정보 |
| **제공받는 자의 보유 및 이용기간** | **연동 해제 시 지체없이 파기** |

(카카오 가이드 원문 요구사항: 제공받는자·제공목적·보유기간은 강조 표기 — 위 굵은 글씨로 반영됨)

## 함께 전달할 것

1. **연동 해제 화면 URL**: `https://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io/oauth/disconnect`
   (토큰 없이 바로 접속 가능 — 지금 바로 스크린샷 가능)
2. **동의 화면**: 실제 OAuth 흐름에서만 접근 가능
   (`/oauth/consent?token=...`). 토큰 없이 접근하면 400을 반환한다.
3. **Redirect URI 2개** (mcpId=3606 확정):
   - `https://tools.kakao.com/api/v1/applied-mcps/3606/authorize/oauth:callback`
   - `https://playmcp.kakao.com/api/v1/applied-mcps/3606/authorize/oauth:callback`

## 현재 상태 (2026-08-27 기준)

- 코드: Redirect URI 경로 수정(mcpId=3606 하드코딩), 동의 화면, 연동 해제 화면,
  정보 흐름 방향에 맞춘 제공목적/제공항목 재작성 완료 (server/auth.py)
- 서버: `stock-quiz-mcp-kakaotools`를 Git 소스 방식으로 재생성(구 mcpId 3556 →
  신규 3606). Endpoint URL은 동일하게 유지됨(디스코드 제출 URL 안 바뀜)
- 배포: 원격에서 OAuth challenge, DCR, authorize, 동의 화면, token,
  인증된 `tools/list`까지 검증 완료
- 랭킹: OAuth/플랫폼 식별값을 점수 키로 우선 사용하고, 없으면 닉네임 fallback
- 영속화: OAuth 클라이언트/동의/토큰과 주간 점수 스냅샷 모두 JSON으로 저장
- **디스코드 제출 전 사람이 할 일**:
  1. `/oauth/disconnect` 스크린샷
  2. 이 문서의 동의문 표 + Redirect URI 2개 + 스크린샷을 디스코드에 전송
