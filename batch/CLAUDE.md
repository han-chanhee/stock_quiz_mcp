# 모듈 D: batch — 일일 데이터 갱신

## 담당

- `daily.py`: 매 영업일 18시 실행 (Docker 동일 이미지, 다른 커맨드)
  - 산출물 (전부 batch/data/*.json, DB 금지):
    1. `top20_kr.json`, `top20_us.json` — price_quiz 풀 (시총 TOP20 스냅샷)
    2. `movers_{market}_{period}.json` — 기간 5종 × 시장 2종 등락률 랭킹
       (today는 리프레셔가 장중 갱신, 나머지는 배치)
    3. `sector_top100.json` — guess_company 풀 (섹터 10 × 시총 TOP10)
    4. `aliases.json` — 종목 별칭 테이블 (초기 수동 시드 + 신규 종목 이름 자동 추가)
    5. `reasons.json` — 풀 전체(랭킹+섹터100) 종목의 원인 팩트 프리캐싱.
       네이버 뉴스 API로 종목당 최신 1건 수집. Reason 모델 준수 —
       source_url 없으면 저장 거부. 런타임 뉴스 호출은 어디에도 없다.
- 섹터 매핑: KRX 업종분류 → contracts의 Sector enum 10개로 수동 매핑 테이블 유지
  (`sector_map.json`, 애매한 종목은 제외가 원칙)

## 의존

- MarketClient Protocol (mock으로 개발, 조립 시 실 클라이언트 주입)
- 산출 JSON은 반드시 `RankingItem`/`StockSnapshot` 스키마로 직렬화 (model_dump_json)

## 완료 정의

- `tests/test_batch.py` 통과: mock 입력으로 배치 1회 실행 → 산출 JSON 4종이
  스키마 재파싱(model_validate) 성공, 섹터 풀이 정확히 10섹터 × ≤10종목,
  as_of 타임스탬프 존재
