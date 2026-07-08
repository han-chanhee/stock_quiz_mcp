# 배포 가이드 (PlayMCP)

Stock Quiz Dictionary(주식사전 퀴즈) MCP 서버. Streamable HTTP, stateless.
서버·배치는 **동일 이미지, 다른 커맨드**로 돈다.

## 1. 사전 준비

- Docker (Linux 배포 권장 — uvloop/httptools로 성능 확보)
- 실 데이터 배치용 API 키 (서버 기동 자체엔 불필요, 아래 3 참고)

## 2. 이미지 빌드 & 구동

```bash
docker build -t stock-quiz-mcp .
docker run -d -p 8000:8000 --name quiz stock-quiz-mcp
curl http://localhost:8000/health      # {"status":"ok","stale":false,...}
```

- MCP 엔드포인트: `http://<host>:8000/mcp`
- 시크릿은 **이미지에 굽지 않는다**(.dockerignore가 `.env` 제외). 런타임 주입:
  ```bash
  docker run -d -p 8000:8000 \
    -e KIS_APP_KEY=... -e KIS_APP_SECRET=... \
    -e NAVER_CLIENT_ID=... -e NAVER_CLIENT_SECRET=... \
    stock-quiz-mcp
  ```

## 3. 데이터 (batch/data/*.json)

- 이미지에는 **실 KR 데이터 스냅샷이 baked**되어 있어 키 없이도 서비스된다.
- 서버 요청 경로는 이 캐시에서만 조립한다(외부 API 호출 0 — 성능 100ms 원칙).
- **매 영업일 18시 갱신**은 동일 이미지의 배치 커맨드로:
  ```bash
  docker run --rm \
    -e KIS_APP_KEY=... -e KIS_APP_SECRET=... \
    -e NAVER_CLIENT_ID=... -e NAVER_CLIENT_SECRET=... \
    -v $(pwd)/data:/app/batch/data \
    stock-quiz-mcp python -m batch
  ```
  (볼륨 마운트로 갱신하거나, 배치 후 이미지 재빌드)
- 서버 기동 시 `batch/data/*.json`을 전수 `model_validate` 검증한다.
  스키마 오류 파일이 하나라도 있으면 **기동 중단**(썩은 데이터 방지).
- 당일 파일 부재 시 있는 파일로 기동하되 `/health`의 `stale=true`로 노출.

## 4. 헬스체크

`GET /health` → `{status, stale, data_as_of}`. Dockerfile HEALTHCHECK 내장.
`stale=true`면 배치가 밀린 것 — 운영자가 즉시 인지 가능.

## 5. 툴 (5개)

`price_quiz`, `top_gainers_quiz`, `top_losers_quiz`, `guess_company`, `submit_answer`
— annotations 5필드 명시, description 영문+"주식사전 퀴즈" 병기, readOnlyHint=true.

## 6. 알려진 제약 (v1)

- **해외(US) 비활성**: 실 KIS 해외 엔드포인트 미검증이라 잠금. `market=US` 요청은
  "준비 중" 안내를 반환한다. 재활성화: `server/handlers.py`의 `US_ENABLED=True` +
  `clients/kis.py`의 `TODO(US)` 구현.
- **guess_company 풀**: KIS 시총 랭킹이 페이지당 30건이라 현재 섹터 풀이 상위 종목
  중심(7섹터/약 20종목). 확장하려면 `top_market_cap` 페이지네이션 +
  `batch/data/sector_map.json` 보강.
- **단일 인스턴스**: 인메모리 store 전제(Redis 금지 — 스펙). 멀티 워커/수평 확장 불가.
  단일 워커로 구동할 것(`python -m server.main` = 단일 워커).
- **성능 게이트(원격 측정 필요)**: 로컬 Windows는 uvloop 부재로 동시성 계측이
  비대표적이다. 단일요청 지연은 ~23ms(p99 32ms, json_response). 동시 100요청
  평균 ≤100ms는 **Linux 배포 환경에서 재측정**할 것(게이트 2).

## 7. 로컬 개발/테스트

```bash
pip install -r requirements.txt        # Python 3.12+
pytest -q                              # 52 tests, 실 키 불필요(mock)
python -m server.main                  # 로컬 기동 (PORT/HOST env)
```
