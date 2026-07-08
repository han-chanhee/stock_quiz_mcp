# 모듈 A: clients — 외부 API 래퍼

## 담당

- `kis.py`: KIS API 비동기 클라이언트 (httpx)
  - 토큰 발급/자동 갱신, 초당 호출 한도 준수(토큰 버킷), 지수 백오프 재시도
  - 필요한 조회: 국내 시총 상위, 등락률 순위(기간별), 종목 현재가, (미국) 해외주식 시세/순위
- `mocks/`: 각 응답의 실측 기반 mock 픽스처 (JSON). 실 응답을 그대로 잘라서 만든다.

## 반환 계약

- 모든 공개 함수는 `contracts.schemas`의 `StockSnapshot` / `RankingItem` 리스트만 반환한다.
- API 원본 dict를 밖으로 노출하지 않는다.

## 인터페이스 (이 시그니처를 유지할 것)

```python
class MarketClient(Protocol):
    async def top_market_cap(self, market: Market, n: int) -> list[StockSnapshot]: ...
    async def top_movers(self, market: Market, period: Period, direction: str, n: int) -> list[RankingItem]: ...
    async def snapshot(self, ticker: str, market: Market) -> StockSnapshot | None: ...
```

- `MockMarketClient` 를 같은 Protocol로 구현해 mocks/ 픽스처를 반환하게 한다.
  다른 모듈(B, D)은 이 mock으로 개발한다.

## 금지

- 캐싱 로직 넣지 말 것 (server/cache.py 담당)
- 퀴즈 로직 넣지 말 것 (services 담당)

## 완료 정의

- `tests/test_clients.py`: MockMarketClient가 스키마 유효한 데이터를 반환하는지,
  토큰 버킷이 한도를 지키는지(시간 mock) 테스트 통과
- 실 키가 있으면 스모크 1회 실행 로그를 남기되, 테스트는 키 없이 통과해야 함
