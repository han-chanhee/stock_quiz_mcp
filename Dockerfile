# Stock Quiz Dictionary(주식사전 퀴즈) MCP — 서버/배치 공용 이미지.
# 서버:  기본 CMD (streamable-http)
# 배치:  docker run <img> python -m batch          (동일 이미지, 다른 커맨드)
#
# ★ linux/amd64로 빌드할 것 (PlayMCP in KC는 arm64 거부).
#   GitHub Actions 워크플로가 platforms=linux/amd64로 빌드한다.
FROM python:3.12-slim

# PORT/HOST/호스트보호는 이미지에 구워 넣는다.
# 이유: PlayMCP in KC 등록 폼에 환경변수·포트 입력란이 없어 런타임 주입이 불가.
#  - PORT=8080         : 관리형 컨테이너 플랫폼 관례 포트
#  - DISABLE_HOST_PROTECTION=1 : KC 도메인 요청이 Host 보호에 막히지 않도록(공개·읽기전용)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    DISABLE_HOST_PROTECTION=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# 헬스체크: PORT 환경변수를 참조(8080 기본, Fly 등에서 PORT=8000이면 그쪽을 확인)
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8080'))" || exit 1

# 서버 기동(기동 시 batch/data 전수 검증 → 실패 시 중단)
CMD ["python", "-m", "server.main"]
