# 카카오클라우드(KakaoCloud) 배포 가이드

공모전 규정: **카카오클라우드에 배포된 MCP 서버**여야 정상 응모로 인정됨.
가장 단순한 방법 = **VM(가상머신) 1대 + Docker**. (Fly에서 옮기는 것뿐, 코드/데이터 동일)

---

## A. 카카오클라우드 준비 (콘솔에서)
1. https://kakaocloud.com 가입 + 프로젝트 생성 + 결제수단 등록
2. **Virtual Machine** 인스턴스 생성
   - 이미지: **Ubuntu 22.04**
   - 타입: 소형(vCPU 2 / RAM 2~4GB면 충분)
   - **키페어(SSH 키) 생성/등록** — 다운로드한 .pem 보관
   - 공인 IP(Public IP) 할당 **ON**
3. **보안 그룹(Security Group)** 인바운드 규칙 추가
   - TCP **22** (SSH, 내 IP만 권장)
   - TCP **8000** (MCP, 0.0.0.0/0)

## B. 프로젝트를 VM에 올리기 (내 PC에서)
`.venv` / `.env` 제외하고 압축해 업로드합니다. (PowerShell)
```powershell
# 프로젝트 폴더에서 (압축 — .venv/.env/scratchpad 제외)
tar --exclude=.venv --exclude=.env --exclude=scratchpad --exclude=.git `
    -czf ..\quiz.tar.gz .
# VM으로 전송 (키경로/IP 본인 것으로)
scp -i C:\경로\키.pem ..\quiz.tar.gz ubuntu@<VM공인IP>:~/
```

## C. VM에서 실행 (SSH 접속 후)
```bash
ssh -i C:\경로\키.pem ubuntu@<VM공인IP>

# 압축 풀기
mkdir -p quiz && tar -xzf quiz.tar.gz -C quiz && cd quiz

# 키를 환경변수로 (본인 값으로 교체)
export KIS_APP_KEY="..."
export KIS_APP_SECRET="..."
export NAVER_CLIENT_ID="..."
export NAVER_CLIENT_SECRET="..."

# 배포 스크립트 실행 (Docker 설치+빌드+실행 자동)
bash deploy_vm.sh
```
끝나면 출력되는 헬스체크 URL로 확인:
```
curl http://<VM공인IP>:8000/health   →  {"status":"ok","stale":false,...}
```

## D. PlayMCP 재등록
- **MCP Endpoint** 를 카카오클라우드 주소로 변경:
  `http://<VM공인IP>:8000/mcp`
  (PlayMCP는 http:// 허용. `DISABLE_HOST_PROTECTION=1`이 이미 들어가 IP 접속 OK)
- "정보 불러오기" → 툴 2개(quiz, submit_answer) 잡히는지 확인
- 나머지 등록값(이름/식별자/설명/대화예시)은 기존과 동일

## E. (선택) HTTPS
http로도 등록되지만 https를 원하면:
- **Cloudflare Tunnel**(무료, 도메인/인증서 불필요) — VM에서 `cloudflared tunnel` 실행
- 또는 도메인 + Caddy로 자동 인증서
- 또는 카카오클라우드 **Load Balancer + 인증서**

## 운영 메모
- 재시작 자동(`--restart unless-stopped`) — VM 재부팅해도 컨테이너 살아남음
- 로그: `sudo docker logs -f quiz`
- 재배포: 코드 갱신 후 `bash deploy_vm.sh` 다시
- 일일 데이터 갱신(배치): `sudo docker run --rm -e KIS_APP_KEY=... ... stock-quiz-mcp python -m batch`
  (또는 VM crontab에 등록)
