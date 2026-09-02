# 툴 명세 (2개 확정)

> 개정: 출제 4종(price/gainers/losers/guess_company)을 **모드 1개(`quiz`)로 통합**.
> 사용자 입력을 3모드(주가/시장/종목)로 강제하고, 응답에 모드 설명을 함께 준다.
> (내부 구현·힌트·채점 로직은 그대로 재사용. handlers.QuizMode → QuizType 매핑.)

공통: 정답은 서버(store)에만 저장. 출제 응답에 정답 절대 미포함.
모든 출력 마크다운, 권유 문장 금지, 기준시각 표기.

## 1. quiz — 통합 출제 (3모드)

- Input:
  - `mode` (필수): `주가` | `시장` | `종목`
  - `market` (KR|US, 기본 KR) — 현재 US는 잠금(안내 메시지 반환)
  - `period` (today|yesterday|1w|1m|1y, 기본 today) — 시장 모드에서 사용
  - `sector` (Sector enum 10개 중 택1, 미지정 시 랜덤) — 종목 모드에서 사용
- 동작 (모드별 라우팅):
  - `주가`: 시총 TOP20에서 랜덤 1종목, 현재가를 정답으로 고정. 힌트 UP/DOWN. 국내 주가는 1만원 단위 반올림 정답.
  - `시장`: 해당 기간 상승/하락 랭킹 1~5위 중 랜덤. **방향(상승/하락)은 랜덤**. 힌트 초성.
  - `종목`: 섹터 10 × 시총 TOP10 풀에서 랜덤. 섹터+현재가+시총순위 힌트. 힌트 초성.
- Output: 마크다운 = **[모드 설명 1줄] + [퀴즈 문제] + quiz_id**. 정답 미포함.

## 2. submit_answer — 채점

- Input: `quiz_id` (str), `answer` (str — 종목명 또는 가격 숫자)
- 동작:
  1. store에서 quiz_id 조회. 없으면 NOT_FOUND, TTL 만료면 EXPIRED
  2. 종목명 판정: 공백/대소문자/한영 정규화 + 별칭 테이블
  3. 가격 판정: 국내 주가는 1만원 단위 반올림, 비원화 주가는 ±3% 룰 (경계 3.0% 포함)
  4. WRONG → attempts 증가, 점수 감점, 단계별 Hint + 내 점수/순위 + TOP3 반환
  5. CORRECT → solved=true, 점수 가점, MiniAnalysis + 내 점수/순위 + TOP3 + next_actions 반환
- Output: 마크다운(GradingResult 기반)
- 개인 이용: 사용자가 받은 quiz_id는 정답 처리 후 재제출해도 점수를 다시 반영하지 않는다.

## annotations 공통값

| 필드 | 값 |
|---|---|
| readOnlyHint | true (전 툴) |
| destructiveHint | false |
| openWorldHint | false |
| idempotentHint | false |

## description 작성 규칙

- 영문, "Stock Quiz Dictionary(주식사전 퀴즈)" 병기, 1,024자 이내
