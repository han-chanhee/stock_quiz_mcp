# 개인정보 제3자 제공 동의문 — 디스코드 제출용

카카오 개발 가이드 6장 1-c에 따라, 아래 내용을 팀별 디스코드 채널에 전달해
카카오 개인정보보호팀 검토를 받는다. (사람이 직접 제출 — 코드로 자동화하지 않음)

## 지금 바로 아래 "디스코드에 복붙할 내용"만 보내면 됩니다

환경변수(`OAUTH_ENABLED`) 설정은 PlayMCP in KC 콘솔 서버 상세 페이지에서
편집 메뉴를 못 찾은 상태(2026-08-19) — 이건 제출과 별개 문제이므로 뒤로
미루고, 코드·URI가 이미 확정된 지금 상태로 먼저 제출한다.

## 제출 전 확인할 것

- [x] `mcpId` 확정: **3556** (stock-quiz-mcp-kakaotools 서버 ID)
- [ ] (보류) PlayMCP 콘솔에서 `OAUTH_ENABLED=1`, `OAUTH_MCP_ID=3556` 설정 방법
      확인 — 상세 페이지에 편집 버튼이 안 보임. 재배포로도 변경 안 됨.
      카카오 측에 문의 필요할 수 있음(디스코드 제출 시 같이 물어봐도 됨).
- [ ] 아래 동의문 문구를 서비스 실정에 맞게 최종 검토(특히 "제공 목적"이 실제
      수집·활용 범위와 일치하는지)
- [x] Redirect URI 2개 확정(아래 "함께 전달할 내용" 참고, mcpId=3556 반영됨)

---

## 디스코드에 복붙할 내용 (지금 바로 제출 가능)

```
[개인정보 제3자 제공 동의문 검토 요청]

서비스: 주식대결 (stock-quiz-mcp-kakaotools, mcpId: 3556)

■ 개인정보 제3자 제공 동의문
- 제공받는자: (주) 카카오
- 제공목적: 주식대결 서비스 제공을 위한 Kakao Tools 연동 및 관리, 서비스 호출
  및 응답 처리, 서비스 품질 향상 및 개선, 고객 문의 대응
- 제공항목: Kakao Tools 연동을 위한 인증 정보(이용자 식별자)
- 제공받는 자의 보유 및 이용기간: 연동 해제 시 지체없이 파기

■ Redirect URI (2개 모두 등록 요청)
- https://tools.kakao.com/api/v1/applied-mcps/3556/authorize/oauth:callback
- https://playmcp.kakao.com/api/v1/applied-mcps/3556/authorize/oauth:callback

■ 연동 해제 화면
https://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io/oauth/disconnect

■ 동의 화면
구현 완료. 실 서비스 OAuth 흐름에서 발급되는 토큰으로만 접근 가능한 구조라
사전 캡처가 어렵습니다(토큰 없이 접근 시 400 응답). 필요하시면 흐름 설명
또는 스크린샷을 추가로 안내드리겠습니다.

■ 추가 문의
PlayMCP in KC 콘솔에서 기존 등록된 서버에 환경변수(OAUTH_ENABLED 등)를
추가하는 방법을 찾지 못했습니다. 상세 페이지에는 "등록된 환경변수가
없습니다"만 표시되고 편집 UI가 보이지 않습니다. 재배포로도 변경되지
않는 것으로 확인했습니다. 방법을 안내해주시면 감사하겠습니다.
```

## 개인정보 제3자 제공 동의문

| 항목 | 내용 |
|---|---|
| **제공받는자** | **(주) 카카오** |
| **제공목적** | 주식대결 서비스 제공을 위한 Kakao Tools 연동 및 관리, 서비스 호출 및 응답 처리, 서비스 품질 향상 및 개선, 고객 문의 대응 |
| **제공항목** | Kakao Tools 연동을 위한 인증 정보(이용자 식별자) |
| **제공받는 자의 보유 및 이용기간** | **연동 해제 시 지체없이 파기** |

(카카오 가이드 원문 요구사항: 제공받는자·제공목적·보유기간은 강조 표기 — 위 굵은 글씨로 반영됨)

## 함께 전달할 것

1. **연동 해제 화면 URL**: `https://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io/oauth/disconnect`
   (토큰 없이 바로 접속 가능 — 지금 바로 스크린샷 가능)
2. **동의 화면**: `OAUTH_ENABLED=1` 배포 후에만 실제 토큰으로 접근 가능
   (`/oauth/consent?token=...`). 시간이 급하면 이번 제출에서는 생략하고
   "동의 화면 구현 완료, 실 서비스 흐름에서 발급되는 토큰으로만 접근 가능"이라고
   설명 텍스트로 대체 가능.
3. **Redirect URI 2개** (mcpId=3556 확정):
   - `https://tools.kakao.com/api/v1/applied-mcps/3556/authorize/oauth:callback`
   - `https://playmcp.kakao.com/api/v1/applied-mcps/3556/authorize/oauth:callback`

## 현재 상태 (2026-08-19 기준)

- 코드: Redirect URI 경로 수정(mcpId=3556 반영), 동의 화면, 연동 해제 화면 구현 완료
  (server/auth.py)
- 배포: OAuth는 `OAUTH_ENABLED` 미설정으로 기본 비활성 상태로 배포됨
- **디스코드 제출 전 사람이 할 일**:
  1. PlayMCP 콘솔에서 `stock-quiz-mcp-kakaotools` 환경변수에
     `OAUTH_ENABLED=1`, `OAUTH_MCP_ID=3556` 추가 후 재배포
  2. `/oauth/disconnect` 스크린샷
  3. 이 문서의 동의문 표 + Redirect URI 2개 + 스크린샷을 디스코드에 전송
- **알려진 한계** (승인 후 별도 작업 필요, 이번 제출을 막지는 않음):
  동의/토큰 저장이 인메모리라 재배포 시 초기화됨(영속화 미구현)
