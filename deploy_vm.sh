#!/usr/bin/env bash
# 카카오클라우드(또는 임의 Ubuntu) VM에서 실행: Docker 설치 → 이미지 빌드 → 실행.
# 사용법:
#   1) 이 프로젝트 폴더를 VM에 올린다(.venv/.env 제외).
#   2) 키 4개를 환경변수로 export 하거나 아래 값을 채운다.
#   3) bash deploy_vm.sh
set -euo pipefail

# ── 키 (미리 export 해두면 그 값을 쓰고, 아니면 여기 채우기) ──
: "${KIS_APP_KEY:=}"
: "${KIS_APP_SECRET:=}"
: "${NAVER_CLIENT_ID:=}"
: "${NAVER_CLIENT_SECRET:=}"

if [ -z "$KIS_APP_KEY" ] || [ -z "$KIS_APP_SECRET" ]; then
  echo "ERROR: KIS_APP_KEY / KIS_APP_SECRET 환경변수를 설정하세요." >&2
  exit 1
fi

# ── 1. Docker 설치 (없으면) ──
if ! command -v docker >/dev/null 2>&1; then
  echo "[1/4] Docker 설치..."
  curl -fsSL https://get.docker.com | sudo sh
fi

# ── 2. 이미지 빌드 ──
echo "[2/4] 이미지 빌드..."
sudo docker build -t stock-quiz-mcp .

# ── 3. 기존 컨테이너 정리 ──
echo "[3/4] 기존 컨테이너 정리..."
sudo docker rm -f quiz >/dev/null 2>&1 || true

# ── 4. 실행 (재시작 정책 + 키 주입 + Host 보호 해제) ──
echo "[4/4] 컨테이너 실행..."
sudo docker run -d --name quiz --restart unless-stopped -p 8000:8000 \
  -e KIS_APP_KEY="$KIS_APP_KEY" \
  -e KIS_APP_SECRET="$KIS_APP_SECRET" \
  -e NAVER_CLIENT_ID="$NAVER_CLIENT_ID" \
  -e NAVER_CLIENT_SECRET="$NAVER_CLIENT_SECRET" \
  -e DISABLE_HOST_PROTECTION=1 \
  stock-quiz-mcp

sleep 3
IP=$(curl -s ifconfig.me || echo "<VM_PUBLIC_IP>")
echo ""
echo "완료! 확인:"
echo "  헬스체크:  curl http://$IP:8000/health"
echo "  MCP 등록:  http://$IP:8000/mcp"
echo ""
echo "로그: sudo docker logs -f quiz"
