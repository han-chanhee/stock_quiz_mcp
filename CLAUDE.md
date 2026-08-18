# 프로젝트 규칙 (Claude Code 상시 컨텍스트)

이 파일은 매 세션 자동으로 읽힙니다. 프로젝트마다 다른 내용만 아래에 채웁니다.

## 역할 분담

- Claude Code: 계획 수립, 검증 기준 정의, 구현 결과의 계획 대비 검증
- Codex: 계획에 따른 실제 구현, 검증 명령 실행, 1차 자가 점검

Claude Code는 직접 구현 코드를 작성하지 않습니다. 계획과 검증만 담당합니다.

## 계획 작성 규칙

계획 요청 시 `.harness/plan.md` 를 템플릿 형식 그대로 작성합니다.

- 태스크 하나는 수정 파일 5개 이하 규모로 쪼갭니다.
- 수정 대상 파일의 정확한 경로를 명시합니다.
- 새로 만드는 함수와 클래스는 시그니처를 계획 단계에서 확정합니다.
- 태스크마다 "이 명령이 통과하면 완료"에 해당하는 검증 명령을 적습니다.
- 태스크 간 의존성을 depends_on 으로 명시합니다. 없으면 none 으로 적고 병렬 실행 대상이 됩니다.
- 태스크마다 금지 사항을 최소 1개 적습니다.

## 검증 규칙

검증 요청 시 아래만 확인하고 코드 스타일은 지적하지 않습니다.

- 계획에 적힌 내용 중 누락된 것
- 계획에 없는데 임의로 추가된 변경
- 인터페이스 시그니처 불일치
- 금지 사항 위반
- 검증 명령의 실제 통과 여부

첫 줄에 PASS 또는 FAIL 만 쓰고, FAIL이면 수정 지시를 번호 목록으로 씁니다.

---
아래부터 프로젝트마다 채우세요.
---

## 이 프로젝트는

(한두 줄. 무엇을 하는 프로젝트인지)

## 코드 컨벤션

- 언어와 버전:
- 포맷터 / 린터:
- 테스트 실행 명령:
- 디렉터리 구조 규칙:
- 네이밍 규칙:

## 절대 건드리면 안 되는 경로

- (예: `migrations/`, `deploy/`, `.env*`, 운영 스크립트)

## 금지 사항

- 계획에 없는 파일 생성
- 의존성 패키지 임의 추가
- 기존 테스트 수정 또는 삭제


# HN 하네스 사용 설명서

Claude Code가 계획과 검증을, Codex가 구현을 맡는 개발 하네스입니다.
사람이 하는 일은 계획 파일을 읽고 고치는 것뿐입니다.

---

## 1. 이게 무엇인가

### 문제

AI 코딩 도구에 큰 작업을 한 번에 맡기면 두 가지가 무너집니다.

- **품질**: 인터페이스와 완료 조건이 불명확하면 모델이 헤매고, 재작업이 반복됩니다.
- **속도**: 태스크를 하나씩 순차로 돌리면 매번 사람이 기다립니다. 결과를 눈으로 다 검토하면 병목이 사람으로 옮겨옵니다.

### 해법

세 가지를 분리합니다.

| 단계 | 담당 | 이유 |
|---|---|---|
| 계획 수립 | Claude Code + 사람 | 여기 5분이 뒤의 재작업 30분을 막습니다 |
| 구현 | Codex (병렬) | 독립 태스크를 동시에 돌려 대기 시간을 없앱니다 |
| 검증 | 테스트 + Claude Code | 사람이 diff를 다 읽지 않아도 되게 합니다 |

### 왜 계획한 쪽이 검증하는가

같은 모델이 짜고 스스로 검토하면 같은 맹점을 공유합니다.
계획을 세운 주체가 결과를 봐야 "요구한 걸 안 했다"를 잡아냅니다.

마찬가지로 테스트 케이스는 구현한 쪽이 아니라 계획한 쪽이 정의합니다.
구현 주체가 테스트도 짜면 자기 구현에 맞춰 통과하는 테스트를 씁니다.

---

## 2. 전체 흐름

```
[사람] 요청
   ↓
[Claude Code] 코드베이스 탐색 → plan.md 초안
   ↓
[사람] plan.md 검토·수정          ← 유일한 개입 지점
   ↓
[hn] 의존성 파싱 → 실행 순서 결정
   ↓
[Codex] 태스크 병렬 구현 (worktree 분리)
   ↓
[Codex] 검증 명령 실행
   ↓
[Claude Code] 계획 대비 검증 → PASS / FAIL
   ↓
FAIL이면 지적사항 넣어 재시도 (기본 2회)
   ↓
PASS면 변경 파일 목록 표시 → 병합
```

---

## 3. 설치

### 3.1 사전 준비

WSL(Ubuntu) 환경에서 아래 세 가지가 필요합니다.

```bash
claude --help    # Claude Code
codex --help     # Codex CLI
git --version    # Git
```

`codex` 가 없다면:

```bash
mkdir -p ~/.npm-global
npm config set prefix ~/.npm-global
echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
npm install -g @openai/codex
```

두 CLI 모두 로그인이 필요합니다. WSL과 윈도우는 별개 환경이므로, 윈도우에서 로그인했더라도 WSL 안에서 다시 해야 합니다.

```bash
claude    # 로그인 절차 진행
codex     # 로그인 절차 진행
```

Git 신원 설정 (로컬 전용이면 아무 값이나 무방):

```bash
git config --global user.name "이름"
git config --global user.email "메일주소"
```

### 3.2 전역 설치 (최초 1회)

```bash
cd <다운로드한 폴더>
chmod +x install.sh
./install.sh
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
hn
```

도움말이 출력되면 성공입니다.

설치 결과:

```
~/.harness-global/templates/    프롬프트와 CLAUDE.md 템플릿
~/bin/hn                        실행 로직
```

---

## 4. 프로젝트 준비

### 4.1 프로젝트 위치

**프로젝트는 반드시 WSL 내부(`~/projects/` 등)에 두세요.**

`/mnt/c/` 아래(윈도우 디스크)에서는 두 가지 문제가 발생합니다.

- `git init` 시 `chmod ... Operation not permitted` 오류
- git 작업이 체감될 정도로 느려짐

윈도우 폴더를 옮기려면:

```bash
mkdir -p ~/projects
cp -r /mnt/c/Users/<사용자>/<경로>/<프로젝트> ~/projects/
cd ~/projects/<프로젝트>
```

윈도우 탐색기에서 WSL 파일에 접근하려면 주소창에 다음을 입력합니다.

```
\\wsl$\Ubuntu\home\<사용자명>\projects
```

### 4.2 .gitignore 먼저

빌드 산출물과 캐시가 커밋되면 diff가 지저분해져 검증 품질이 떨어집니다.
git 초기화 **전에** 만드세요.

```bash
cat > .gitignore << 'EOF'
__pycache__/
*.pyc
.hypothesis/
.pytest_cache/
.venv/
venv/
node_modules/
*.log
.env
.harness/logs/
EOF
```

### 4.3 Git 초기화

```bash
git init
git add -A
git commit -m "before harness"
git status
```

`nothing to commit, working tree clean` 이 나오면 준비 완료입니다.

### 4.4 하네스 설치 (프로젝트당 1회)

```bash
hn init
```

생성되는 것:

```
CLAUDE.md                       # 이미 있으면 건너뜀
.harness/plan.template.md
.harness/prompts/implement.md
.harness/prompts/verify.md
.harness/logs/
```

### 4.5 CLAUDE.md 채우기

**이 단계를 건너뛰면 하네스가 제대로 동작하지 않습니다.**

`CLAUDE.md` 는 Claude Code가 매 세션 자동으로 읽는 파일입니다.
여기에 규칙이 없으면 계획이 매번 다른 형식으로 나옵니다.

**기존 CLAUDE.md가 이미 있던 프로젝트라면** `hn init` 이 덮어쓰지 않으므로 직접 붙여야 합니다.

```bash
cat ~/.harness-global/templates/CLAUDE.md >> CLAUDE.md
grep -n "plan.md" CLAUDE.md    # 줄 번호가 나오면 성공
```

그다음 아래 세 항목을 프로젝트에 맞게 채웁니다.

| 항목 | 채울 내용 | 안 채우면 |
|---|---|---|
| 이 프로젝트는 | 무엇을 하는 프로젝트인지 한두 줄 | 계획의 맥락이 빗나감 |
| 코드 컨벤션 | 언어·버전, 포맷터, 테스트 실행 명령 | 프로젝트와 다른 스타일의 코드가 나옴 |
| 절대 건드리면 안 되는 경로 | 마이그레이션, 배포 스크립트, 설정 파일 | 모델이 위험한 파일을 수정 |

---

## 5. 일상 사용

설치가 끝나면 매 작업은 세 단계입니다.

### 5.1 계획 만들기

**방법 A: 한 번에 초안 생성**

```bash
hn plan "리프레시 토큰 추가"
```

**방법 B: 대화하며 다듬기 (권장)**

```bash
claude
```

세션 안에서:

```
이 프로젝트에서 [작업 내용]을 하려고 해.
코드베이스 먼저 살펴보고 나랑 상의하면서 계획을 다듬자.
확정되면 .harness/plan.template.md 형식 그대로 .harness/plan.md 에 저장해줘.
구현은 하지 마.
```

복잡하거나 기존 코드를 많이 건드리는 작업은 B가 낫습니다.

> **참고**: 웹 챗(claude.ai)에서 계획을 짜는 것은 권하지 않습니다.
> 파일 경로와 기존 함수 시그니처를 볼 수 없어 추측하게 되고,
> 그것이 Codex가 헤매는 가장 큰 원인입니다.

### 5.2 계획 검토 — 사람이 하는 유일한 일

```bash
code .harness/plan.md
```

**확인할 네 가지:**

1. **파일 경로가 실제로 존재하는가** — 모델이 추측한 경로일 수 있습니다
2. **검증 명령이 복붙해서 그대로 돌아가는가** — 여기가 자동 게이트의 전부입니다
3. **태스크 하나가 수정 파일 5개를 넘지 않는가** — 넘으면 쪼개세요
4. **depends_on이 정확한가** — 틀리면 순서가 꼬입니다

태스크 목록만 빠르게 보려면:

```bash
hn status
```

계획이 마음에 안 들면 그 자리에서 `claude` 를 열어 다듬으세요.

```
.harness/plan.md 의 TASK-002가 너무 큰 것 같은데 둘로 쪼개줘
```

### 5.3 실행

```bash
hn run
```

의존성을 읽어 순서를 잡고, 서로 독립인 태스크는 동시에 실행합니다.
통과하면 변경 파일 목록을 보여주고 병합할지 묻습니다.

특정 태스크만:

```bash
hn run TASK-002
```

---

## 6. 명령어 전체

| 명령 | 설명 | 빈도 |
|---|---|---|
| `hn init` | 프로젝트에 하네스 설치 | 프로젝트당 1회 |
| `hn plan "내용"` | 계획 초안 생성 | 작업마다 |
| `hn status` | 태스크와 의존성 목록 확인 | 필요 시 |
| `hn run` | 전체 실행 (독립 태스크 자동 병렬) | 작업마다 |
| `hn run TASK-001` | 특정 태스크만 실행 | 필요 시 |
| `hn clean` | 남은 worktree와 브랜치 정리 | 필요 시 |

### 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `MAX_RETRY` | 2 | 검증 실패 시 재시도 횟수 |
| `HN_AUTO_MERGE` | 0 | 1이면 병합 확인 없이 자동 병합 |

```bash
MAX_RETRY=3 hn run
HN_AUTO_MERGE=1 hn run
```

---

## 7. plan.md 작성 가이드

계획 파일이 이 하네스의 계약서입니다. 여기 적힌 것만 구현되고, 여기 적힌 것으로만 검증됩니다.

### 7.1 태스크 구조

```markdown
## TASK-001: 설정 파서 추가

- **depends_on**: none
- **수정 대상 파일**:
  - `services/config.py` (신규)
  - `server/main.py` (수정)
- **인터페이스 확정**:
  ```
  def parse_config(path: str) -> Config
  class Config: name: str, retries: int
  ```
- **구현 내용**:
  1. YAML 파일을 읽어 Config 객체로 변환
  2. 파일이 없으면 FileNotFoundError를 그대로 전파
  3. main.py 시작 시 parse_config 호출
- **검증 명령**:
  ```
  pytest tests/test_config.py -q
  ```
- **완료 조건**: 위 명령이 종료 코드 0으로 끝난다
- **금지 사항**:
  - 기존 load_settings() 시그니처 변경 금지
  - 새 의존성 패키지 추가 금지
```

### 7.2 각 항목이 왜 필요한가

| 항목 | 없으면 생기는 일 |
|---|---|
| 수정 대상 파일 | 모델이 관련 없는 파일까지 손댐 |
| 인터페이스 확정 | 태스크 간 시그니처가 어긋나 병합 시 깨짐 |
| 구현 내용 | "적절히 처리" 수준의 모호한 코드가 나옴 |
| 검증 명령 | 자동 게이트가 작동하지 않아 사람이 다 봐야 함 |
| 금지 사항 | 계획 밖 리팩터링이 섞여 diff가 커짐 |
| depends_on | 병렬 실행이 불가능해지거나 순서가 꼬임 |

### 7.3 잘 쓰는 요령

- **"적절히", "필요하면", "알아서" 같은 표현을 쓰지 마세요.** 모호한 만큼 결과가 흔들립니다.
- **태스크는 작게.** 파일 5개가 상한선입니다. 크면 diff 검토가 불가능해지고 실패 시 재시도 비용도 커집니다.
- **독립 태스크를 늘리세요.** `depends_on: none` 이 많을수록 병렬로 돌아 빨라집니다.
- **검증 명령은 반드시 직접 한 번 쳐보세요.** 여기가 틀리면 하네스 전체가 무의미해집니다.

---

## 8. 동작 원리

### 8.1 worktree 분리

태스크마다 별도 git worktree와 `harness/TASK-00N` 브랜치를 만듭니다.

```
프로젝트/                    # 원본, 건드리지 않음
../.hn-wt-프로젝트-TASK-001/  # TASK-001 작업 공간
../.hn-wt-프로젝트-TASK-002/  # TASK-002 작업 공간
```

이 덕분에 Codex 세션 여러 개가 동시에 돌아도 서로 덮어쓰지 않습니다.

### 8.2 재시도 루프

```
Codex 구현 → 검증 명령 실행 → Claude Code 검증
                                    ↓
                              PASS → 종료
                              FAIL → 지적사항을 그대로 다음 프롬프트에 주입 → 재시도
```

**핵심은 사람이 원인을 분석하지 않는 것입니다.**
실패 로그와 원래 계획 항목을 기계적으로 다시 넣습니다.
사람이 원인을 파악해 프롬프트를 새로 쓰는 것이 가장 비싼 행동입니다.

### 8.3 검증의 범위

Claude Code는 코드 스타일을 보지 않습니다. 다섯 가지만 확인합니다.

1. 계획에 적힌 내용 중 누락된 것
2. 계획에 없는데 임의로 추가된 변경
3. 인터페이스 시그니처 불일치
4. 금지 사항 위반
5. 검증 명령의 실제 통과 여부

---

## 9. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `hn: command not found` | PATH 미설정 | `echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc` |
| `git 저장소가 아닙니다` | git init 안 함 | `git init && git add -A && git commit -m wip` |
| `chmod ... Operation not permitted` | `/mnt/c/` 에서 작업 중 | 프로젝트를 `~/projects/` 로 이동 |
| `커밋되지 않은 변경이 있습니다` | 작업 중 변경 잔존 | `git add -A && git commit -m wip` |
| 계획 파일이 생성되지 않음 | CLI 옵션 불일치 | `claude --help` 확인 후 `~/bin/hn` 수정 |
| `변경사항 없음` | 계획이 모호하거나 이미 구현됨 | plan.md의 구현 내용을 더 구체적으로 |
| worktree가 남아 있음 | 중단된 실행 | `hn clean` |
| Codex가 계획 밖 파일 수정 | 금지 사항 미기재 | CLAUDE.md와 plan.md의 금지 사항에 경로 추가 |

### 로그 위치

```
.harness/logs/TASK-001-impl-1.log      # 1차 구현 로그
.harness/logs/TASK-001-verify-1.log    # 1차 검증 결과
```

---

## 10. VS Code에서 작업하기

WSL 터미널에서:

```bash
cd ~/projects/<프로젝트>
code .
```

좌측 하단에 `WSL: Ubuntu` 가 표시되면 정상입니다.

VS Code 안에서 터미널을 열면(``Ctrl + ` ``) 이미 WSL 환경이므로 `hn` 명령을 그대로 씁니다.

**권장 배치:**

- 왼쪽 파일 트리: `.harness/plan.md` 열어 편집
- 아래 터미널 1: `claude` 대화용
- 아래 터미널 2: `hn` 실행용
- 소스 제어 탭: 변경 diff 확인

터미널 추가는 터미널 패널의 `+` 버튼입니다.

---

## 11. 전제 조건과 한계

### 반드시 필요한 것

**실행 가능한 검증 명령.**
테스트가 없는 코드베이스에서는 자동 게이트가 작동하지 않고,
검토 부담만 늘어납니다.

테스트가 없다면 첫 계획의 TASK-001을 "테스트 하네스 구축"으로 잡으세요.

### 한계

- **잘못된 계획은 잡히지 않습니다.** 테스트는 구현이 계획대로 되었는지만 봅니다. 계획 자체가 틀렸으면 완벽하게 구현되고 통과합니다. 계획 검토를 건너뛰면 오류를 잡을 지점이 없습니다.
- **검증도 모델 판단입니다.** PASS가 100% 신뢰할 수 있는 것은 아닙니다. 병합 직전 변경 파일 목록을 훑는 최소 확인선은 남겨두시길 권합니다.
- **사용량 소모가 큽니다.** 태스크마다 구현과 검증이 각각 호출되고, 재시도 시 배가됩니다.
- **CLI 옵션은 변합니다.** 두 도구 모두 업데이트가 잦습니다. 동작이 이상하면 `--help` 로 확인 후 `~/bin/hn` 을 수정하세요.

### 안전장치

자동 병합(`HN_AUTO_MERGE=1`)을 쓰실 거면 작업 전에 되돌림 지점을 만드세요.

```bash
git tag before-run
# 문제 발생 시
git reset --hard before-run
```

---

## 12. 하네스 자체 수정

```
~/.harness-global/templates/CLAUDE.md          # 프로젝트 규칙 템플릿
~/.harness-global/templates/plan.template.md   # 계획 포맷
~/.harness-global/templates/prompts/*.md       # 구현·검증 프롬프트
~/bin/hn                                       # 실행 로직
```

여기만 고치면 모든 프로젝트에 즉시 반영됩니다.
프로젝트마다 복사본을 두지 않는 것이 이 구조의 이유입니다.

단, 이미 `hn init` 을 실행한 프로젝트의 `.harness/prompts/` 는
설치 시점의 복사본이므로, 프롬프트를 고쳤다면 다시 복사해야 합니다.

```bash
cp ~/.harness-global/templates/prompts/*.md .harness/prompts/
```

---

## 13. 대안

이 하네스가 무겁게 느껴진다면 다른 선택지도 있습니다.

**Claude Code 내장 Agent Teams**

설정 파일 한 줄이면 켜집니다. Codex 없이 Claude Code 세션 여러 개가 팀으로 협업합니다.

`~/.claude/settings.json`:

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

켠 뒤에는 자연어로 "이 작업을 팀으로 나눠서 진행해줘"라고 말하면 됩니다.

**수동 운영**

스크립트 없이 Claude Code로 계획을 세워 `plan.md` 로 저장하고,
Codex를 열어 "plan.md의 TASK-001만 구현해"라고 지시하는 방식입니다.
설치가 필요 없고, 태스크가 적을 때는 오히려 빠릅니다.

세팅 부담이 크다고 느껴지면 수동으로 몇 번 돌려보고,
반복이 지겨워지는 시점에 하네스로 돌아오는 순서를 권합니다.