# 모듈 E: server — FastMCP 조립 계층

## 담당

- `main.py`: FastMCP 엔트리
  - Streamable HTTP, `stateless_http=True` (MCP 세션 미사용. quiz 상태는 store가 별도 보관 —
    프로토콜 세션과 앱 상태는 별개임을 주석으로 명시)
  - 툴 5개 등록: price_quiz, top_gainers_quiz, top_losers_quiz, guess_company, submit_answer
  - name/description/inputSchema/annotations는 contracts/tool_specs.md 를 그대로 따른다
  - 의존성 주입 지점 한 곳: `build_app(client: MarketClient, store: QuizStore)`
    → 테스트/조립에서 mock↔실구현 교체가 이 함수 하나로 끝나야 함
  - `/health` 엔드포인트 추가 (배포 헬스체크용)
- `cache.py`: TTL 캐시
  - batch/data/*.json 기동 시 model_validate로 전수 검증 후 메모리 적재.
    검증 실패 파일이 하나라도 있으면 기동 중단 (루트 규칙 13)
  - 배치 실패로 당일 파일이 없으면 전일 파일로 기동하되 stale=true 플래그 유지
  - today 랭킹 리프레셔: 장중 3회(10:00/13:00/15:40) 스케줄 태스크. 5분 폴링 금지
- `/health` 응답에 {status, stale, data_as_of} 포함 — 낡은 데이터로 버티는 중임을
  운영자가 즉시 알 수 있게
- 후처리: 모든 툴 응답 말미에 데이터 기준시각(as_of) 자동 삽입.
  퀴즈 서비스 특성상 투자 면책은 미니분석 포함 응답에만 삽입:
  "본 내용은 퀴즈/정보 제공이며 투자 권유가 아닙니다."

## 응답 형식

- 마크다운만. 에러도 정제 한 줄 ("잠시 후 다시 시도해주세요"). 스택트레이스 노출 금지.
- result 크기 최소화 (PlayMCP 가이드).

## 완료 정의

- `tests/test_server.py` 통과: build_app(mock, store)로 5개 툴 목록 노출,
  출제→오답→힌트→정답→미니분석+2택 풀 시나리오 왕복,
  존재하지 않는 quiz_id → NOT_FOUND 메시지
- 로컬 MCP Inspector 접속 확인 (수동, 로그 남길 것)
