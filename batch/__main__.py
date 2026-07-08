"""배치 실행 엔트리포인트: `python -m batch`.

실 KIS 클라이언트 + 네이버 원인 수집으로 batch/data/*.json 전체를 갱신한다.
매 영업일 18시 크론/스케줄러가 이 커맨드를 호출한다(서버와 동일 이미지, 다른 커맨드).
"""

from __future__ import annotations

import asyncio

from clients.kis import KISClient

from .daily import DailyBatch
from .reasons import NaverReasonProvider


async def _main() -> None:
    client = KISClient()
    try:
        batch = DailyBatch(client, reason_provider=NaverReasonProvider())
        await batch.run()
        print("배치 완료: batch/data/*.json 갱신됨")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
