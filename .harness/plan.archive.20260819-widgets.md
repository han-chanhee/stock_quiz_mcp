# 작업 계획

> Claude Code가 초안을 만들고 사람이 승인합니다. Codex는 이 파일의 태스크 단위로만 작업합니다.

## 목표

`quiz`/`submit_answer` 응답을 카카오 Kakao Tools 위젯(OpenAI ChatKit widgets 스펙)으로
반환해, 랭킹(TOP5+본인 순위)·힌트·정답 정보가 LLM 요약 과정에서 누락되지 않고 그대로
사용자에게 보이게 한다.

## 배경 (이 계획이 필요한 이유)

- Kakao Tools Preview 실측 결과: 위젯 없이 마크다운 텍스트만 반환하면 ChatGPT가 응답을
  재가공(요약)하면서 랭킹 섹션("이번 정답으로 N점 획득! / 주간 TOP5")을 임의로 생략함이
  확인됨. 서버 응답 자체(curl 직접 호출)에는 랭킹이 정확히 들어있었으므로 서버 결함이
  아니라 "LLM이 텍스트를 가공한다"는 카카오 플랫폼 특성 문제.
- 위젯을 쓰면 카카오 가이드(`Kakao Tools 개발 가이드.pdf` 3장)에 따라
  "제공자가 구성한 위젯의 내용을 그대로 답변으로 사용, LLM이 답변을 가공하지 않음"이
  보장된다.
- 리더보드/퀴즈출제 위젯 JSON 스펙은 HANDOFF.md §7(2026-08-15 조사 완료)에 이미
  실측 기반(1차 출처: `@openai/chatkit` 렌더러 타입 + `chatkit-python` SDK)으로
  정리되어 있음 — 재조사 불필요, 그 스펙을 그대로 구현에 옮긴다.
- FastMCP 툴 함수의 반환 문자열이 `content[0].text`에 그대로 들어감을 이번 세션에서
  실측 확인(로컬 테스트 서버, `/tmp/widget_test_server.py`). 즉 툴 함수가
  `json.dumps(payload, ensure_ascii=False)`를 반환하면 카카오 가이드 별첨의
  `tools/call` 응답 예시와 정확히 같은 구조가 된다 — FastMCP 쪽 별도 처리 불필요.
- `server/CLAUDE.md`의 "응답 형식: 마크다운만"은 위젯 도입으로 개정 대상이 된다
  (이 계획 완료 후 문서 갱신 필요 — TASK-004에서 처리).

## 공통 제약

- 건드리면 안 되는 경로: `contracts/schemas.py` (위젯은 카카오 전용 dict 스펙이라
  pydantic 모델화하지 않는다 — 아래 TASK-001 참고)
- 추가 금지 의존성: 없음. 표준 라이브러리 `json`만 사용.
- 위젯 JSON은 **정확한 프로퍼티명**을 그대로 써야 한다(HANDOFF.md §7 표 참고). 오타는
  에러 없이 조용히 텍스트로 강등되므로 반드시 그 표의 철자를 그대로 옮긴다.
- 루트 컨테이너는 `Card`/`ListView`/`Basic` 중 하나만, `status` 프로퍼티는 위젯 어디에도
  넣지 않는다(카카오가 자동 삽입).
- 매 위젯 응답에 `copy_text`(카카오톡 공유하기용 간단 마크다운)를 반드시 포함한다.
- 기존 마크다운 응답 로직(`QuizOutcome.markdown`, `SubmitOutcome.markdown`)은 삭제하지
  않는다 — `copy_text` 조립에 재사용하고, 위젯 JSON 파싱 실패 시나리오 대비 폴백으로도
  남긴다.
- 주석/독스트링/커밋 메시지는 한국어.
- 기존 테스트 수정·삭제 금지(추가만).

---

## TASK-001: server/widgets.py 신설 — 위젯 dict 조립 순수 함수

- **depends_on**: none
- **수정 대상 파일**:
  - `server/widgets.py` (신규)
  - `tests/test_widgets.py` (신규)
- **인터페이스 확정**:
  ```python
  # server/widgets.py
  def quiz_question_widget(
      quiz_id: str,
      mode_intro: str,
      question_md: str,
      expires_in_sec: int = 1800,
  ) -> dict:
      """출제 응답 위젯. HANDOFF.md §7 '퀴즈 출제 위젯 JSON' 스펙을 따른다.
      반환값은 {"widget": {...}, "copy_text": "...", "name": "quiz_question"} 형태."""
      ...

  def wrong_answer_widget(hint_text: str, attempts: int) -> dict:
      """오답 응답 위젯. 간단한 Card + Text 구성.
      {"widget": {...}, "copy_text": "...", "name": "wrong_answer"}"""
      ...

  def correct_answer_widget(
      answer_name: str,
      price_line: str,
      rank_line: str,
      reason_line: str,
      earned_score: int | None,
      leaderboard: "LeaderboardSnapshot | None",
      next_actions: list[str],
  ) -> dict:
      """정답 응답 위젯. 미니분석 + (있으면) 점수·TOP5 랭킹 + 다음 액션.
      {"widget": {...}, "copy_text": "...", "name": "correct_answer"}"""
      ...

  def leaderboard_table_rows(leaderboard: "LeaderboardSnapshot") -> dict:
      """TOP5를 Table 컴포넌트로 조립(1차 시도). 렌더러 미지원 시 TASK-003에서
      ListView+Row 폴백으로 교체한다 — 이 함수는 Table 버전만 담당."""
      ...

  def to_content_text(payload: dict) -> str:
      \"\"\"json.dumps(payload, ensure_ascii=False)로 직렬화.
      모든 위젯 반환값은 이 함수를 거쳐 MCP 툴의 str 반환값이 된다.\"\"\"
      ...
  ```
- **구현 내용**:
  1. HANDOFF.md §7의 "퀴즈 출제 위젯 JSON" 샘플(Card 루트, Icon+Badge 난이도 표시,
     Title, Text, Divider, Markdown, Caption)의 **레이아웃 구조만** `quiz_question_widget`
     으로 옮긴다. 샘플의 `Title.value`("이 기업의 종목명은?")는 그 샘플이 예시로 든
     "종목 퀴즈" 1종에 국한된 문구다 — 이 함수는 주가/시장/종목 3개 퀴즈 모드
     공통으로 호출되므로 `Title`을 특정 퀴즈 문구로 하드코딩하면 안 된다.
     `Title.value`는 인자로 받은 `mode_intro`(예: "📈 주가 퀴즈 — ...")에서 유도하거나
     "주식대결 퀴즈" 같은 범용 문구로 고정하고, 실제 문제 본문은 `question_md`
     인자를 그대로 `Markdown`/`Text` 컴포넌트에 넣어 표시한다. `\n`이 `Text.value`
     안에서 줄바꿈되는지 불확실하므로(HANDOFF.md 경고), 여러 줄이 필요한 부분은
     `Col` + 개별 `Text`로 분해한다.
  2. `wrong_answer_widget`은 `Card` 루트에 `Text`(오답 안내) + `Badge`(힌트 텍스트,
     color="warning") 정도의 최소 구성으로 만든다(과설계 금지 — 오답은 자주 반복되는
     응답이라 가볍게).
  3. `correct_answer_widget`은 `Card` 루트에 `Title`(정답 종목명) + 미니분석 3줄
     (`Text` 각각) + `Divider` + (leaderboard가 있으면) 점수 `Badge` + TOP5 표
     (`leaderboard_table_rows` 호출 결과를 자식으로 삽입) + 다음 액션
     `Button`(`onClickAction` 없이 label만 — 카카오는 툴 재호출 버튼을 지원하지
     않으므로 텍스트 안내로만 표시. HANDOFF.md "폼 계열은 쓸모없다" 참고).
  4. `leaderboard_table_rows`는 HANDOFF.md의 미검증 `Table` 컴포넌트로 1차 구현한다
     (`Table` 루트 안에 `Table.Row`(header=True) 1개 + 데이터 행들, 각 `Table.Cell`에
     순위/닉네임/점수). 정확한 스펙은 HANDOFF.md §7 표의 `Table` 행을 그대로 따른다.
  5. 모든 함수는 순수 함수로 만든다 — `QuizCache`/`ScoreStore` 등을 직접 참조하지
     않고, 호출부(TASK-002)가 이미 계산한 값만 인자로 받는다.
  6. `to_content_text`는 단순 `json.dumps` 래퍼. 위젯 JSON에 `ensure_ascii=False`
     필수(한글 깨짐 방지).
- **검증 명령**:
  ```
  pytest tests/test_widgets.py -q
  ```
- **완료 조건**: 각 위젯 함수가 반환한 dict를 `json.dumps` 후 재파싱해도 무결(round-trip),
  최상위 키가 정확히 `widget`/`copy_text`/`name` 3개, `widget.type`이 `Card`/`ListView`
  중 하나, `status` 키가 어디에도 없음을 검증하는 테스트 포함. **추가로**:
  `quiz_question_widget("QZ-1", "📈 주가 퀴즈 — ...", "**삼성전자**의 현재 주가는
  얼마일까요?")`처럼 종목 퀴즈가 아닌 다른 모드 문구를 넣었을 때, 위젯 안 어디에도
  "이 기업의 종목명은?"(또는 그 어떤 특정 퀴즈 모드에 고정된 문구)이 하드코딩되어
  나타나지 않고 `question_md` 인자 내용이 실제로 위젯에 반영됨을 검증하는 테스트 포함.
- **금지 사항**:
  - `contracts/schemas.py` 수정 금지
  - `server/handlers.py`/`server/main.py` 수정 금지(이 태스크는 순수 함수만 추가)
  - `status` 프로퍼티를 위젯 JSON 어디에도 넣지 말 것
  - `quiz_question_widget`의 `Title`(또는 다른 컴포넌트)에 특정 퀴즈 모드 전용
    문구를 하드코딩하지 말 것(3개 모드 공통 호출 함수임을 반드시 지킬 것)

---

## TASK-002: handlers.py가 위젯 payload를 함께 반환하도록 확장

- **depends_on**: TASK-001
- **수정 대상 파일**:
  - `server/handlers.py` (수정)
  - `tests/test_server.py` (수정 — 케이스 추가)
- **인터페이스 확정**:
  ```python
  @dataclass
  class QuizOutcome:
      quiz_id: str
      markdown: str
      widget: dict | None = None   # None이면 quiz_id 없는 안내 응답(US 차단 등)

  @dataclass
  class SubmitOutcome:
      verdict: Verdict
      markdown: str
      analysis: MiniAnalysis | None = None
      attempts: int = 0
      next_actions: list[str] = field(default_factory=list)
      leaderboard: LeaderboardSnapshot | None = None
      widget: dict | None = None   # WRONG/CORRECT일 때만 채움. EXPIRED/NOT_FOUND는 None
  ```
- **구현 내용**:
  1. `_register`(출제 등록 공통 경로)에서 기존 `markdown` 조립에 더해
     `widgets.quiz_question_widget(...)`를 호출해 `QuizOutcome.widget`에 채운다.
     `_us_guard`/섹터 없음 등 quiz_id 없는 안내 응답은 `widget=None`으로 둔다
     (이런 안내는 굳이 위젯화하지 않음 — 발생 빈도 낮고 텍스트로 충분).
  2. `submit_answer`의 WRONG 분기에서 `widgets.wrong_answer_widget(...)`를 호출해
     `SubmitOutcome.widget`에 채운다.
  3. `submit_answer`의 CORRECT 확정 분기(`was_first=True`)에서
     `widgets.correct_answer_widget(...)`를 호출해 채운다. `nickname` 공백이라
     `leaderboard=None`인 경우도 위젯 자체는 만든다(점수 섹션만 생략).
  4. EXPIRED/NOT_FOUND/이미 solved 분기는 `widget=None`으로 유지한다(발생 빈도 낮고,
     이런 오류성 응답까지 위젯화하는 건 과설계).
  5. 기존 `markdown` 필드와 조립 로직(`_render_correct` 등)은 그대로 둔다 — 삭제하지
     않는다(위젯의 `copy_text` 조립 시 재사용, 폴백용).
- **검증 명령**:
  ```
  pytest tests/test_server.py -q
  ```
- **완료 조건**: 정답 확정 응답의 `widget`이 None이 아니고 `widget["name"] ==
  "correct_answer"`, 오답 응답의 `widget["name"] == "wrong_answer"`, 출제 응답의
  `widget["name"] == "quiz_question"`임을 테스트로 확인. 기존 markdown 필드 값은
  이전과 동일하게 유지됨(회귀 없음).
- **금지 사항**:
  - `services/` 모듈 시그니처 변경 금지
  - `store`/`score_store` 인터페이스 변경 금지
  - 기존 `markdown` 조립 로직 삭제 금지

---

## TASK-003: main.py 툴 함수가 위젯 JSON을 반환하도록 전환

- **depends_on**: TASK-002
- **수정 대상 파일**:
  - `server/main.py` (수정)
  - `tests/test_server.py` (수정 — 케이스 추가, fastmcp 통합 경로는 기존처럼 별도
    유지)
- **인터페이스 확정**:
  ```python
  # main.py의 quiz/submit_answer 툴 함수 반환값 변경
  # 기존: return handlers.quiz(...).markdown
  # 변경: outcome = handlers.quiz(...)
  #       if outcome.widget is not None:
  #           return widgets.to_content_text(outcome.widget)
  #       return outcome.markdown   # 위젯 없는 안내 응답은 기존처럼 마크다운
  ```
- **구현 내용**:
  1. `quiz` 툴 함수: `outcome.widget`이 있으면 `widgets.to_content_text(outcome.widget)`
     반환, 없으면 기존처럼 `outcome.markdown` 반환.
  2. `submit_answer` 툴 함수: 동일 패턴 — `outcome.widget`이 있으면 위젯 JSON 문자열,
     없으면(EXPIRED/NOT_FOUND 등) 기존 마크다운 문자열.
  3. `_safe` 예외 래퍼는 그대로 유지한다 — 위젯 조립 중 예외가 나도
     `_SAFE_ERROR`(정제된 한 줄 텍스트)로 떨어지게 한다(스택트레이스 노출 금지 원칙
     유지).
  4. 툴 `description`에 위젯 사용 사실을 추가할 필요는 없다(카카오 가이드상 위젯
     여부는 파트너 재량이고 description은 툴 호출 판단용이라 무관).
- **검증 명령**:
  ```
  pytest tests/test_server.py -q
  pytest -q
  ```
- **완료 조건**: 전체 테스트 통과. 로컬 서버 기동 후 curl로 `quiz`/`submit_answer`
  호출 시 `content[0].text`가 유효한 JSON(위젯 payload)으로 파싱됨을 수동 확인
  (검증 담당이 배포 전 별도 실행).
- **금지 사항**:
  - `_safe` 래퍼 로직 변경 금지
  - `/health` 응답 구조 변경 금지
  - `build_app` 공개 시그니처 변경 금지

---

## TASK-004: Preview 실측 후 Table→ListView 폴백 + 문서 갱신

- **depends_on**: TASK-003 (배포 및 Kakao Tools Preview 실측 완료 후 진행 —
  Claude Code가 검증 단계에서 사용자와 함께 Preview 결과를 확인한 뒤 착수)
- **배경**: HANDOFF.md 경고대로 `Table` 컴포넌트는 렌더러엔 있고 Python SDK엔 없어
  카카오 렌더러가 지원하는지 불확실하다. TASK-001에서 Table로 1차 구현했으나,
  Preview 실측 결과 조용히 텍스트로 강등되면 `ListView`+`Row`+`justify:"between"`
  조합(HANDOFF.md §7 "리더보드 위젯 JSON" 샘플, 이미 검증된 컴포넌트만 사용)으로
  교체해야 한다. 이 태스크는 실측 결과에 따라 두 시나리오 중 하나만 수행한다.
- **수정 대상 파일**:
  - `server/widgets.py` (수정 — `leaderboard_table_rows`를 대체하거나 병행)
  - `server/CLAUDE.md` (수정 — "응답 형식: 마크다운만" 문구를 위젯 도입 반영해 갱신)
  - `tests/test_widgets.py` (수정 — 케이스 추가)
- **구현 내용**:
  - **시나리오 A (Table 정상 렌더링 확인됨)**: 변경 없음. 이 태스크는 문서 갱신만
    수행(`server/CLAUDE.md`의 "응답 형식" 절에 "위젯(JSON) 우선, quiz_id 없는 안내
    응답만 마크다운 폴백"으로 갱신).
  - **시나리오 B (Table이 텍스트로 강등됨)**: `leaderboard_table_rows`를
    `leaderboard_listview_rows`로 교체(또는 함수 내부에서 조건 없이 ListView 방식으로
    변경) — HANDOFF.md §7 "리더보드 위젯 JSON" 샘플의 `ListView`+`ListViewItem`+
    `Row`(`justify:"between"`, 닉네임 `Text`에 `flex:1`+`truncate:true`, 상위 3위는
    `Badge` 색 차등)를 그대로 옮긴다. `correct_answer_widget`이 이 함수를 호출하도록
    `server/widgets.py`를 수정한다.
- **검증 명령**:
  ```
  pytest -q
  ```
- **완료 조건**: 전체 테스트 통과. Preview에서 리더보드 위젯이 실제로 카드/리스트
  형태로 렌더링됨(조용한 텍스트 강등이 아님)을 검증 담당이 스크린샷 또는 대화 로그로
  확인.
- **금지 사항**:
  - `contracts/schemas.py` 수정 금지
  - Table/ListView 어느 쪽이든 `status` 프로퍼티 추가 금지

---

## 이번 계획에 포함하지 않는 것

1. **차트 퀴즈용 `Chart`/스파크라인 위젯** — HANDOFF.md에 이미 별도 조사 결과가
   있으나(유니코드 블록 스파크라인이 가장 안전), 이번 계획은 랭킹 노출 문제 해결에
   한정한다.
2. **위젯 버튼의 `onClickAction`(URL 이동)** — 카카오는 툴 재호출을 지원하지 않아
   "다음 퀴즈" 같은 액션을 실제 버튼으로 만들 수 없다. 텍스트 안내로 유지.
3. **`Image`/외부 CDN 활용** — HANDOFF.md에 "아마 됨, 도메인 제한 미확인"으로 남아있는
   미검증 항목. 이번 범위 밖.
4. **OAuth 활성화** — 별도 트랙, 개인정보보호팀 승인 대기 중(이전 계획에서 코드는
   이미 준비됨).

## 전체 검증 (모든 태스크 완료 후)

```
pytest -q                                     # 전체 통과
python -m server.main &                       # 로컬 기동
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"quiz","arguments":{"mode":"주가","nickname":"검증"}}}'
# content[0].text 가 유효 JSON(위젯 payload)인지 확인
```

배포 후 Kakao Tools Preview에서 "kakaoTalk-quiz 툴을 사용해서 답변해줘" 식으로
명시 호출해 위젯이 실제로 카드/리스트 UI로 렌더링되는지(텍스트로 강등되지 않는지)
최종 확인한다.

**롤백**: `git status`로 변경 파일 확인 후 필요 시 `git checkout -- <file>`로 개별 되돌림.
