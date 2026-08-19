# 작업 계획

> Claude Code가 초안을 만들고 사람이 승인합니다. Codex는 이 파일의 태스크 단위로만 작업합니다.
> 이번 사이클은 사용자가 "질문 없이 알아서 끝까지 진행"을 명시했으므로, 이후 설계 디테일은
> Claude Code가 기존 코드 패턴과 카카오 위젯 스펙(HANDOFF.md §7)을 기준으로 직접 결정한다.

## 목표

12개(모드별 출제 3종 분리로 실질 14개) 화면 전부를 카카오 위젯으로 완성하고,
웰컴/사용법 안내를 위한 `help` 툴을 신설한다. 사용자 관점에서 완결된 UX가 되도록
한다.

## 배경

- Preview 실측(2026-08-19)으로 두 가지가 확정됨:
  1. 정답 위젯의 `Table` 컴포넌트는 카카오 렌더러에서 텍스트로 강등됨 → 이미
     `Col`+`Row`+`Badge`+`Text` 조합으로 교체 완료(server/widgets.py 커밋 완료).
  2. `submit_answer` 툴이 카카오 ChatGPT LLM에 의해 호출되지 않는 경우가 확인됨
     — 이는 카카오 측 LLM 판단 로직 문제로, 서버 코드로 해결 불가능. 이번 계획의
     범위 밖(사용자가 "호출 문제는 별개, 내일 논의"로 명시). 이번 계획은 서버가
     정확한 위젯을 반환하는 것까지만 책임진다.
- 화면 목록(사용자와 합의된 14개, 이전 대화에서 확정):
  1. 웰컴/사용법 안내 (신규 `help` 툴)
  2. 모드/닉네임 선택 안내 (quiz가 mode/nickname 없이 호출될 때)
  3. 퀴즈 출제 — 주가 모드
  4. 퀴즈 출제 — 시장 모드
  5. 퀴즈 출제 — 종목 모드
  6. 오답
  7. 정답
  8. 이미 정답 나온 퀴즈 (재제출)
  9. 만료된 퀴즈
  10. 존재하지 않는 quiz_id
  11. 해외(US) 시장 차단
  12. 섹터에 종목 없음
  13. 종목 데이터 준비중
  14. 모드 미선택(방어적)

## 공통 제약

- 건드리면 안 되는 경로: `contracts/schemas.py`
- 추가 금지 의존성: 없음. 표준 라이브러리만.
- 위젯 JSON은 HANDOFF.md §7에 정리된 검증/미검증 컴포넌트 구분을 따른다.
  `Table`은 쓰지 않는다(이미 강등 확인됨). `ListView`/`ListViewItem`은 루트·자식
  전용 문구가 있어 `Card` 안에 중첩하지 않는다(기존 leaderboard_listview_rows와
  동일 원칙 — 실제로는 `Col`+`Row`로 구현).
- 루트 컨테이너는 `Card` 하나로 통일한다(이미 모든 기존 위젯이 `Card`).
- `status` 프로퍼티는 위젯 어디에도 넣지 않는다.
- 매 위젯에 `copy_text` 필수 포함.
- 기존 마크다운 필드(`QuizOutcome.markdown`, `SubmitOutcome.markdown`)는 유지한다
  (위젯이 없는 극히 드문 폴백 경로 대비, `copy_text` 조립 재사용).
- 기존 테스트 수정·삭제 금지(추가만). 코드 구현은 Claude Code가 직접 작성한다
  (하네스 역할 분담과 달리 이번엔 실시간 반복 검증이 필요해 Codex 위임 대신
  직접 구현 — 사용자가 다음날 검토하는 흐름이라 속도 우선).
- 주석은 한국어.

---

## TASK-001: widgets.py에 신규 화면 8개 함수 추가

- **수정 대상 파일**: `server/widgets.py`, `tests/test_widgets.py`
- **신규 함수**:
  ```python
  def welcome_widget() -> dict: ...                          # 화면 1
  def mode_selection_widget() -> dict: ...                    # 화면 2
  def price_quiz_widget(quiz_id, question_md, expires_in_sec=1800) -> dict: ...   # 화면 3
  def market_quiz_widget(quiz_id, question_md, expires_in_sec=1800) -> dict: ...  # 화면 4
  def company_quiz_widget(quiz_id, question_md, expires_in_sec=1800) -> dict: ... # 화면 5
  def already_solved_widget() -> dict: ...                    # 화면 8
  def expired_quiz_widget() -> dict: ...                      # 화면 9
  def quiz_not_found_widget() -> dict: ...                    # 화면 10
  def us_blocked_widget() -> dict: ...                        # 화면 11
  def sector_empty_widget(sector_label: str) -> dict: ...     # 화면 12
  def company_pool_empty_widget() -> dict: ...                # 화면 13
  def mode_unknown_widget() -> dict: ...                      # 화면 14
  ```
- **구현 내용**:
  1. `price_quiz_widget`/`market_quiz_widget`/`company_quiz_widget` 3개는
     기존 `quiz_question_widget`을 대체한다(공통 틀은 그대로, 모드별 바디만 분리).
     공통 틀: 아이콘+난이도 배지 → Spacer → Title("주식대결 퀴즈") → 모드 인트로
     → Divider → **모드별 바디** → Divider → quiz_id → Caption.
     - `price_quiz_widget` 바디: 숫자 입력 강조 — `question_md`(이미 자릿수 힌트
       포함)를 `Markdown`으로 표시하고, 그 아래 `Badge`(color="info")로
       "숫자만 입력하세요" 안내 추가.
     - `market_quiz_widget` 바디: `question_md`(기간+방향+등락률 포함)를
       `Markdown`으로 표시. 등락률 부호(+/-)에 따라 `Badge` color를
       success(+)/danger(-)로 다르게 준다(`question_md` 텍스트에서 부호 문자를
       파싱하지 않고, 호출부인 handlers.py가 이미 아는 값을 인자로 넘기게
       시그니처를 확장한다 — 아래 TASK-002에서 처리).
     - `company_quiz_widget` 바디: `question_md`가 이미 "- 섹터: ... / - 현재가:
       ... / - 시총 ...위권" 형태(마크다운 리스트)이므로 그대로 `Markdown`
       컴포넌트에 넣는다(기존 quiz_question_widget과 동일 처리 — 이 모드는
       원래도 리스트형이라 변경 최소).
  2. `welcome_widget()`: Card 루트. Title("주식대결에 오신 걸 환영해요!") +
     3모드 소개(각 모드를 `Row`(Icon+Text)로 나열: 주가=📈, 시장=📊, 종목=🏢) +
     Divider + 닉네임 안내 Text + Caption(발화 예시: "주가 모드로 퀴즈 내줘.
     닉네임은 OOO야") + Caption(주간 랭킹 초기화 안내).
  3. `mode_selection_widget()`: Card 루트. Text("모드와 닉네임을 알려주세요") +
     3모드를 Badge 3개로 나열(주가/시장/종목) + Caption(발화 예시).
  4. `already_solved_widget()`: Card 루트. Text("🏁 이미 정답이 나온 퀴즈입니다.") +
     Caption("새 퀴즈를 출제해주세요").
  5. `expired_quiz_widget()`: Card 루트. Text("⏰ 만료된 퀴즈입니다.") +
     Caption("30분이 지나면 quiz_id가 사라져요. 새 퀴즈를 출제해주세요").
  6. `quiz_not_found_widget()`: Card 루트. Text("❓ 존재하지 않는 quiz_id입니다.") +
     Caption("quiz_id를 다시 확인해주세요").
  7. `us_blocked_widget()`: Card 루트. Text("🌏 해외 종목 퀴즈는 준비 중입니다.") +
     Caption("지금은 국내(KR) 퀴즈만 즐길 수 있어요").
  8. `sector_empty_widget(sector_label)`: Card 루트. Text(f"🗂️ '{sector_label}'
     섹터는 아직 준비된 종목이 부족해요.") + Caption("섹터를 비워두면 전체에서
     출제해 드려요").
  9. `company_pool_empty_widget()`: Card 루트. Text("🗂️ 회사 맞히기 데이터를
     준비 중이에요.").
  10. `mode_unknown_widget()`: Card 루트. Text("주가 / 시장 / 종목 중에서
      골라주세요.").
  11. 모든 함수는 `{"widget": {...}, "copy_text": "...", "name": "..."}` 형태를
      반환한다. `name`은 함수 역할을 딴 스네이크케이스(예: "welcome",
      "mode_selection", "already_solved" 등).
- **검증 명령**: `.venv/bin/pytest tests/test_widgets.py -q`
- **완료 조건**: 신규 함수 12개(3개 출제 모드 포함) 전부 테스트로 스키마 검증
  (round-trip JSON, `status` 키 없음, 최상위 키 3개 정확).

---

## TASK-002: handlers.py가 신규 위젯을 모든 안내/에러 경로에 연결

- **수정 대상 파일**: `server/handlers.py`, `tests/test_server.py`
- **구현 내용**:
  1. `_register`가 `state.quiz_type`에 따라 `price_quiz_widget`/
     `market_quiz_widget`/`company_quiz_widget` 중 하나를 호출하도록 분기
     (기존 `quiz_question_widget` 단일 호출을 대체).
  2. `market_quiz_widget` 호출 시 등락률 부호를 위젯에 전달하기 위해, `_register`
     또는 `movers_quiz` 경로에서 `state.answer.change_pct`(이미 StockSnapshot에
     존재)의 부호를 계산해 넘긴다. `market_quiz_widget` 시그니처에
     `change_pct: float` 인자를 추가한다(TASK-001에서 확정한 시그니처를
     이 값 하나만큼 확장 — 순수 함수 원칙 유지, cache/store 직접 참조 안 함).
  3. `quiz()`가 mode 또는 nickname 없이 호출되는 경우를 대비해 시그니처를
     `mode: QuizMode | None = None`, `nickname: str | None = None`으로 바꾸고,
     둘 중 하나라도 None이면 `mode_selection_widget()`을 담은 `QuizOutcome`을
     즉시 반환한다(quiz_id 없음). **주의**: FastMCP 툴 시그니처(main.py)도
     함께 Optional로 바꿔야 한다(TASK-003에서 처리) — 안 바꾸면 이 경로 자체가
     생기지 않는다.
  4. `_us_guard`가 `us_blocked_widget()`을 쓰도록 변경.
  5. `guess_company`의 섹터 없음 분기가 `sector_empty_widget(sector.value)`를,
     풀 자체가 빈 분기가 `company_pool_empty_widget()`을 쓰도록 변경.
  6. `quiz()`의 방어적 else 분기가 `mode_unknown_widget()`을 쓰도록 변경.
  7. `submit_answer`의 EXPIRED 분기가 `expired_quiz_widget()`을,
     NOT_FOUND 분기가 `quiz_not_found_widget()`을, 이미 solved인 두 분기
     (L219-222, L227-232)가 `already_solved_widget()`을 쓰도록 변경.
  8. 위 모든 변경에서 `QuizOutcome`/`SubmitOutcome`의 `widget` 필드를 채운다.
     기존 `markdown` 필드 문구는 그대로 유지(폴백/`copy_text` 원본 — 이미
     각 위젯의 copy_text가 markdown 문구를 그대로 재사용하도록 TASK-001에서
     구성했으므로 이중 유지 비용 없음).
- **검증 명령**: `.venv/bin/pytest tests/test_server.py -q`
- **완료 조건**: 14개 화면 각각에 대응하는 `QuizOutcome`/`SubmitOutcome`이
  `widget is not None`이고 정확한 `name`을 가짐을 테스트로 확인.
- **금지 사항**: `compare_and_solve`/`record_attempt`(store) 시그니처 변경 금지.

---

## TASK-003: help 툴 신설 + main.py가 Optional 파라미터·신규 위젯 반영

- **수정 대상 파일**: `server/main.py`, `tests/test_server.py`
- **구현 내용**:
  1. `quiz` 툴 파라미터를 `mode: QuizMode | None = None`,
     `nickname: str | None = None`으로 변경(TASK-002의 handlers.quiz 시그니처와
     정합). description에 "mode/nickname 생략 시 안내를 반환한다"는 문구 추가.
  2. 신규 `help` 툴 등록:
     ```python
     @mcp.tool(
         name="help",
         description=(
             "Shows how to play 주식대결 (Stock Quiz Battle / 주식사전 퀴즈): "
             "the three quiz modes (주가/시장/종목), why a nickname is needed "
             "for weekly ranking, and example phrases to start. Call this when "
             "the user asks how the quiz works or seems unsure how to start."
         ),
         annotations=ToolAnnotations(title="How to Play", **_COMMON_ANN),
     )
     def help() -> str:
         return widgets.to_content_text(widgets.welcome_widget())
     ```
  3. `quiz`/`submit_answer` 툴 본문은 이미 TASK-002 통해 모든 경로가 `widget`을
     채우므로, `if outcome.widget is not None: return ... else: return
     outcome.markdown` 패턴은 그대로 유지(위젯 없는 경로가 이제 사실상 없어지지만
     안전망으로 남긴다).
- **검증 명령**: `.venv/bin/pytest -q` (전체)
- **완료 조건**: `pytest -q` 전체 통과. `help` 툴이 `build_app`으로 조립된
  FastMCP 인스턴스에서 조회 가능(`await app.get_tool("help")`)함을 테스트로 확인.
  로컬 서버 기동 후 curl로 `help`/`quiz`(mode 생략)/각 오류 경로를 호출해
  위젯 JSON이 유효하게 반환됨을 수동 확인(검증 담당이 직접 수행).
- **금지 사항**: `/health` 응답 구조 변경 금지. `build_app` 공개 시그니처
  (`cache, store, score_store, bank, refresh_client`) 변경 금지.

---

## 전체 검증 (모든 태스크 완료 후)

```
pytest -q
python -m server.main &
# 14개 화면 각각을 curl로 순회 확인 (검증 담당 스크립트로 자동화)
```

**롤백**: `git status`로 변경 파일 확인 후 필요 시 `git checkout -- <file>`.
