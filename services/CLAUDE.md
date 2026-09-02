# 모듈 B: services — 퀴즈 로직

## 담당

- `quiz_bank.py`: 출제 4종 생성
  - price_quiz: 시총 TOP20 풀에서 랜덤, 출제 시점 가격 고정
  - top_gainers/losers_quiz: 기간별 랭킹 1~5위 중 랜덤
  - guess_company: 섹터 10 × TOP10 풀에서 랜덤, 섹터+가격+시총순위 힌트 구성
  - 데이터는 주입받은 MarketClient(Protocol)와 batch 산출 JSON에서만 가져온다
- `grading.py`: 채점 + 힌트
  - 종목명 정규화 판정 (공백/대소문자/별칭 테이블 `aliases.json`)
  - 가격 ±3% 판정
  - 힌트 단계: KR 1차 초성 → 2차 첫 글자 / US 첫 글자+글자수 / price는 UP·DOWN
  - 초성 변환 유틸 포함 (한글 유니코드 분해, 외부 라이브러리 금지)
- `analysis.py`: MiniAnalysis 생성
  - price_line, rank_line은 스냅샷에서 조립
  - reason_line: 뉴스 근거가 주입되지 않으면 반드시 "특별한 재료 확인 안 됨"
  - 권유 문장 생성 절대 금지. 문장 템플릿을 코드에 고정할 것

## 의존

- `contracts.schemas` (모델), MarketClient Protocol (mock 주입)
- store를 직접 import 하지 않는다. QuizState 생성까지만 하고 저장은 server가 한다.

## 완료 정의

- 힌트는 출제 시점에 전 단계를 생성해 QuizState.hints_precomputed에 담는다.
  grading.py의 채점 함수는 저장된 힌트를 꺼내기만 한다 (문자열 연산 금지,
  price UP/DOWN 비교 1회만 예외).

## 완료 정의 (계속)

- `tests/test_services.py` 통과:
  - 출제 4종이 QuizQuestion 스키마 유효 + 정답 미포함 검증
  - 초성 변환 정확성 ("삼성전자"→"ㅅㅅㅈㅈ", "SK하이닉스" 같은 혼합 케이스 포함)
  - ±3% 판정은 hypothesis property 테스트로 경계 포함 수백 케이스 자동 생성
    (정확히 3.0% 케이스 명시 포함)
  - 별칭/오타/한영혼용 판정도 hypothesis로 정규화 불변성 검증
  - reason 미조회 시 "특별한 재료 확인 안 됨" 반환 100%
  - source_url 없는 Reason 생성 시도 → ValidationError 발생 확인
