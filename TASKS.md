# TASKS · CiteReview 구현 작업 목록

> 무엇을 만드는지는 [SERVICE.md](SERVICE.md), 어떻게 만드는지는 [CLAUDE.md](CLAUDE.md), 작업 순서·판단 시점·포기 순서는 [docs/plan.md](docs/plan.md)를 따릅니다.
> - **한 번에 작업 하나만** 합니다. 작업 하나는 확인 한 번으로 끝낼 수 있는 크기입니다.
> - 각 작업의 **확인**을 통과하면 `[ ]`를 `[x]`로 바꿉니다. 확인은 가능한 한 코드(테스트·명령 결과)로 합니다.
> - `tests/`의 확인은 가짜 모델만 씁니다. 실제 LLM 호출이 필요한 확인에는 **(실제 LLM)**이라고 표시했습니다.

---

## 1부 · 최소 경로 — diff 입력 → 가드레일 → 리뷰어 2종 → 출력 검증 → 렌더링 → 평가 실행

평가 diff 3개(03번 `DEP-LLMChain` bug, 04번 E501 style, 10번 문제없는 diff)가 이 경로를 **평가 실행까지** 끝까지 통과하면 1부 완료이며, 이것이 관문 push 기준입니다.

- [x] **A1 외부 의존 확인과 설치** — Bedrock 리뷰 모델·Titan 임베딩·Nova Pro 호출, `create_supervisor` + `create_agent` 호환 확인. `ruff`·`ragas` 설치.
  - 확인: 세 모델 호출이 성공하고 `import ragas`, `ruff --version`이 된다. 호환이 안 되면 CLAUDE.md 4번 대체안으로 정하고 이 줄 아래에 기록.
  - 기록 (1차 시도):
    - ✅ `ruff` 0.17.4 설치 (`python -m ruff`로 실행 — Git Bash PATH에는 `ruff` 명령이 없음)
    - ✅ Titan 임베딩 (1024차원), ✅ Nova Pro 호출
    - ❌ Sonnet 4.5 — `ThrottlingException: Too many tokens per day` (Bedrock 계정 일일 토큰 한도). 코드 문제 아님
    - ✅ `create_supervisor` + `create_agent` 호환 (가짜 모델, 리뷰어 2종 handoff 후 supervisor 종료까지). 가짜 모델은 `bind_tools`를 지원하는 하위 클래스가 필요. `langgraph_supervisor` 내부가 `create_react_agent`를 써서 deprecation 경고가 남 — 우리 코드의 호출은 아님
    - ❌→✅ `ragas` — 과정 venv(3.14)에서는 의존성 `scikit-network` 휠이 없어 실패(기존 패키지 변화 없음). **평가 전용 `.venv-eval`(Python 3.12)**에 설치하고 `requirements-eval.txt`에 고정. `langchain-community` 0.4.2에서 ragas가 쓰는 모듈이 빠져 0.4.1로 고정. 더미 1건 faithfulness를 Nova Pro로 계산해 값(0.6)이 나옴
    - ✅ 리뷰 모델을 `us.anthropic.claude-haiku-4-5-20251001-v1:0`으로 변경 (사용자 결정, 호출 확인 1.4초). CLAUDE.md 1번 갱신
- [x] **A2 설정·스키마·diff 파서** — `requirements.txt`, `ruff.toml`, `src/config.py`, `src/schema.py`(요약 5줄 검증 포함, CLAUDE.md 5번 TODO 처리), `src/diff_parser.py`.
  - 확인: 단위 테스트 통과 — `.py` 300줄 통과·301줄 초과, `.py`+`.md` 혼합에서 `.md` 줄은 세지 않음, 삭제 지점이 다음 줄로 매핑, 요약 4줄 Review는 검증 오류 (`tests/`).
- [x] **A3 평가 diff 3개·코퍼스·검색·도구** — diff 03·04·10 작성(교육용 더미), `data/corpus/pep8.md`(연결표 5개 섹션) + `deprecated_map.md` 색인, `src/retriever.py` 하이브리드 검색, `src/tools.py`의 `ruff_check`·`search_evidence`.
  - 확인: 질의 "from langchain.chains import LLMChain"의 상위 5개에 `DEP-LLMChain`이 있고, 04 diff에 `ruff_check`를 돌리면 `app/prompts.py` 9행 E501이 나온다. 10 diff는 ruff 위반 0건.
  - `deprecated_map.md`의 출처 링크는 **기억으로 URL을 만들지 않습니다.** 실제로 열어서 해당 API가 언급된 것을 확인한 링크만 쓰고, 확인하지 못한 행은 "링크 확인 필요"로 표시한 뒤 사용자에게 알립니다.
  - 기록: 실제 Titan 색인 12청크(6.4초, 재실행 시 재사용 0.1초). "LLMChain"·"create_react_agent"·"E501"·"== None" 질의에서 해당 근거가 1위. 링크는 4행 확인(`create_react_agent`·`LLMChain`·`retrievers`·`langchain_classic`, 앵커까지 확인), **3행 "링크 확인 필요"**(`initialize_agent`·`AgentExecutor`·`memory` — v1 마이그레이션 가이드와 릴리스 노트에 언급 없음)
- [x] **A4 입력 가드레일과 리뷰 그래프** — `src/guardrails.py` 입력 검사(비밀값·PII·인젝션 정규식, `.py` 범위·300줄), `src/reviewers.py`(규칙 기반 planner, 리뷰어 2종, supervisor), `src/agent.py`의 `build_graph(llm=None)`·`run_query()`, `trace`(시각 포함).
  - 확인: 가짜 모델로 `run_query`가 `answer`·`contexts`·`trace` 세 키를 돌려주고, 가짜 AWS 키가 든 diff는 마스킹되어 security 지적이 생긴다 (`tests/`).
  - 기록: `tests/test_agent.py` 3건(세 키·trace, 비밀값·이메일이 모델 입력·출력·trace 어디에도 없음, Java만 있는 diff는 모델 호출 0회로 거절) + `tests/test_input_guardrail.py` 3건 통과. supervisor가 계획된 리뷰어를 빠뜨리면 코드가 직접 실행. 그래프 상태에는 작은 값만 두고 무거운 객체는 `RunContext`에 둠(B2 체크포인터 대비). 비밀번호·API 키 패턴이 `DB_PASSWORD`처럼 접두어 붙은 이름을 놓치던 문제를 테스트로 발견해 고침
- [x] **A5 출력 검증·렌더링·평가 실행** — 출력 검증(근거 실재, 재생성 1회), 렌더러(docs/output_example.md 형식), `src/cli.py`, `evaluation/run_eval.py` 최소판(실행·통과 판정·실행 기록 저장).
  - 확인 **(실제 LLM)**: `run_eval.py --cases 3,4,10`이 세 건을 끝까지 돌려 케이스별 통과/실패, 근거 확인 비율, 응답 시간을 출력한다.
  - 기록: Haiku 4.5 실제 실행 2회 모두 **3/3 통과**, 근거 확인 비율 1.0, 평균 응답 12.9~14.6초. CLI 단독 실행(03번) 약 20초. 렌더러는 `src/render.py`(사용자 결정)로 분리하고 기준 화면과 구조 비교 테스트 3건 추가. 노드 순서를 "출력 검증 → 종합"으로 정함(요약이 제거된 지적을 언급하지 않게). `ruff format`으로 코드 포매팅 통일. 테스트 26건 통과, ruff 0건
- [ ] **A6 관문 push** (최소 경로 완료 후) — README에 제안 요약과 실행 방법 몇 줄, 별도 저장소 `mini-pjt_{본인이름}`에 push.
  - 확인: 새로 clone한 폴더에서 설치 후 A5 명령이 같은 결과를 낸다.

## 2부 · 나머지 붙이기와 평가

- [x] **B1 나머지 평가 diff 17개** — CSV note의 diff 메모대로 작성(교육용 더미). 14번은 `.py` 정확히 300줄(대부분 ruff 위반 없는 단순 코드, 의도한 이슈 1~2개), 12번은 Java만.
  - 확인: traits에 적은 파일·줄 번호가 `diff_parser`로 뽑은 변경 후 줄과 일치하고, 14번은 정확히 300줄, 9·11번은 ruff 위반 0건이다.
- [x] **B2 미들웨어·HITL·가드레일 테스트** — `before_model` 마스킹 미들웨어와 `ModelCallLimitMiddleware`, 체크포인터 + `interrupt` HITL, `--batch`, `github_post_comment` dry-run과 허용 목록, `tests/` 2건.
  - 확인: `pytest tests/`에서 가드레일 테스트 2건([tests/README.md](tests/README.md))이 통과한다.
- [x] **B3 1차 평가** — 20건 × 1회 → `evaluation/round1_report.md`(실패 → 원인 → 개선 계획).
  - 확인 **(실제 LLM)**: 리포트에 통과 수(카테고리별 포함), 근거 확인 비율, 응답 시간, 실패 케이스별 원인이 있다.
- [x] **B4 개선** — 1차 실패 케이스 기준으로 프롬프트·검색 개선, 관련 케이스만 `--cases`로 다시 확인.
  - 확인: 고친 케이스가 `--cases` 실행에서 통과하고, 시도·결과가 round2 리포트 초안에 기록된다.
- [x] **B5 2차 평가와 RAGAS** (남은 순서 1~3: 준비 → 2차 평가 → RAGAS) — 준비: 실행 기록에 RAGAS reference용 청크 원문 저장, `evaluation/run_ragas.py` 작성, **추가 세트 21·22번 diff 작성**(ruff 위반 0건, 교육용 더미), **근거-지적 일치율 계산 추가**(코드, LLM 없음), `run_eval.py`에서 본 세트(1~20)와 추가 세트(21·22)를 나눠 집계하고 forbidden "PEP8-* 근거를 단 style 지적"을 코드로 판정 → 본 세트 20건 × 1회 + 추가 세트 2건 → `evaluation/round2_report.md`, RAGAS 4개.
  - 확인 **(실제 LLM)**: 리포트에 1차 대비 개선폭, guardrail 3/3 여부, RAGAS 값이 있다. `faithfulness`·`answer_relevancy`를 먼저 계산하고, context 2개는 대상 건수와 값 또는 "미측정 + 사유"가 있다. **전체 실행 시간**(20건, 동시 실행 2개)을 재서 1차(4분 31초, 동시 실행 5개)와 나란히 적는다.
  - 기록: 2차 **본 세트 20/20**(1차 9/20, 정정·재채점 15/20), 추가 세트 2/2(리뷰어가 함정에 걸리지 않음), 실행 오류 0건. 전체 실행 시간 2분 55초(22건, 동시 실행 2개). 근거 확인 비율 1.0, 근거-지적 일치율 1.0(8/8), C2 제거 0건. RAGAS faithfulness 0.729(19건, contexts = 검색 청크 + diff hunk + ruff 출력, 코퍼스만이면 0.466) · answer_relevancy 0.264(10건), context_precision 0.711 · context_recall 1.000(각 9건). 4번 1건 실행으로 일치율이 0이 아닌 값으로 집계됨을 먼저 확인
- [ ] **B6 README와 시연 리허설** (남은 순서 4~5) — README §4-5 템플릿 전 절, 인젝션 패턴 목록과 한계, 시연(docs/plan.md 2절).
  - 확인: 템플릿의 모든 제목이 있고 빈 절이 없으며, 시연 3단계를 막힘 없이 끝까지 진행한다. 제출 커밋 첫 줄은 `[제출] MiniPJT`.

## 3부 · 시연용 로컬 GUI (필수 작업 B5·B6이 끝난 뒤에만, 과제 평가 전에 멈춤 — 밀리면 G2 → G1 순으로 포기)

GUI 규칙은 [CLAUDE.md](CLAUDE.md) 6번 "GUI"를 따릅니다. 에이전트·CLI·평가 코드는 고치지 않습니다.

- [x] **G1 GUI 1단계: 기능** (남은 순서 6) — Hallmark 설치(Node 확인, `-g` 없이 이 프로젝트에만, puppeteer·Chrome이 없으면 정적 검사만), `src/gui_server.py`(`http.server`, 127.0.0.1)와 `run.bat`. 단순 HTML로 ① 저장소 폴더 → 커밋 목록(`.py` 변경 줄 수, 300줄 초과 표시) 선택 또는 diff 파일 선택 ② 리뷰 실행(별도 스레드)·trace 노드 진입 폴링으로 진행 표시 ③ 렌더러 결과를 고정폭으로 표시 ④ 승인/거절 버튼(`resume_review`, dry-run + 허용 목록). Hallmark 설치 경로와 `.gitignore` 처리를 CLAUDE.md 2번에 확정해 적음.
  - 확인 **(실제 LLM)**: 03번 diff 리뷰 결과가 화면에 CLI와 같은 형식으로 나오고, 19번 diff는 승인 대기에서 멈춘 뒤 허용 목록 밖 저장소로 승인하면 차단 메시지가 나온다.
  - 기록: 03번은 진행 7단계가 순서대로 켜진 뒤 CLI와 같은 렌더러 출력(`run_query`의 answer)이 나오고 승인 대기. 19번은 보안 3·버그 1 지적 뒤 승인 대기 → `someone/not-allowed`로 승인하면 "게시 허용 목록에 없음"으로 차단, 같은 작업에 두 번째 결정은 거부. 커밋 모드로 day06(`b465f63`, 338줄)을 고르면 목록에 "300줄 초과"가 표시되고 LLM 없이 거절. 서버는 `127.0.0.1`에만 바인딩(netstat 확인), `run.bat` 실행 확인. Hallmark는 `.claude/skills/hallmark/`에 설치(`-g`·puppeteer·Chrome 없음)
- [x] **G2 GUI 2단계: 디자인** (남은 순서 7) — Hallmark custom 또는 차분한 테마 하나로 화면 정리(테마 비교 생략). G1의 버튼·입력·핸들러는 그대로 유지. 히어로·마케팅 문구·구독 폼·페이지 끝 CTA 없음.
  - 확인: G1의 확인 두 가지가 디자인 적용 뒤에도 그대로 되고, 결과 영역의 줄 번호·`>`·`-`/`+` 정렬이 CLI 출력과 같다.
  - 기록: Hallmark `redesign`(custom, app screen, 차분한 웜 그레이 + 뮤트 틸 강조색 하나, 시스템 글꼴로 오프라인 동작, 라이트·다크). 상단 바 + 왼쪽 입력(커밋·diff 파일) + 오른쪽 진행 트랙·결과 원장(고정폭 `pre`, `white-space: pre`)·승인 패널. 요소 id 18개와 핸들러 유지(진행 표시를 `<ol>` 트랙으로, 커밋 변경 칸의 가운뎃점을 쉼표로, 결정 중 버튼 비활성만 추가). sloplint(정적, `--scope app`) 0 FAIL·0 REFLEX·0 WARN — puppeteer·Chrome이 없어 렌더 검사는 하지 않음. 디자인 뒤 03·19번 확인 다시 통과, 결과는 `run_query`의 answer를 `textContent`로 그대로 넣어 CLI와 같은 정렬. HTML/CSS 문자열의 긴 줄 때문에 `ruff.toml`에 `src/gui_server.py`만 E501 예외
- [x] **G3 시연 시나리오 확인** (남은 순서 8) — GUI가 완성되면 docs/plan.md 2절 시연 시나리오에 GUI 장면을 넣을지 확인.
  - 확인: 넣기로 하면 시연 표에 GUI 장면과 확인 포인트가 있고, 넣지 않기로 하면 그 사유가 docs/plan.md에 한 줄 있다.

- [x] **(선택) C2 PEP 8 근거 ruff 교차 확인** (B5 준비에서 2차 전에 적용. 출력 검증 순서는 존재 검사 → C2, 가짜 모델 테스트 1건, 2차 실제 실행 제거 0건) — 출력 검증에서 `PEP8-*` 근거를 단 style 지적은 그 줄에 연결표상 대응하는 ruff 위반이 있을 때만 유지(근거-지적 일치율과 같은 검사를 출력 가드레일로). **2차 평가 전에 할지는 남은 순서 1번(B5 준비)에서 정합니다.** 하면 2차 리포트에 "에이전트 개선"으로 적습니다.
  - 확인: 가짜 모델 테스트 1건 — 대응 ruff 위반이 없는 줄에 `PEP8-maximum-line-length`를 단 style 지적은 제거되고, `ruff:E501`이 있는 줄의 같은 지적은 남는다. 연결표에 없는 PEP 8 섹션을 인용한 지적은 제거하지 않고 그대로 둔다(판정 불가).
- [ ] **(선택) C1 PR 조회** — `github_get_pr`(MCP)로 PR URL 입력 지원.
  - 메모: 이번 제출 범위에서는 하지 않음. 쓰지 않는 의존성 `langchain-mcp-adapters`는 `requirements.txt`에서 뺐으므로, 진행하면 다시 추가합니다.
  - 확인 **(실제 연결)**: 본인 테스트 저장소 PR URL로 `review`가 끝까지 돈다.

## 4부 · 다음 버전 (리뷰 품질 개선, 평가 재실행 필요)

실제 커밋 리뷰(day07 `23402b2`, day03 `b24f206`, day05 `13ee4ae`)에서 발견한 문제를 고칩니다. 2차 평가 결과는 제출 기준으로 그대로 두고, 이 버전은 다시 평가합니다.

- [x] **D1 고치는 방법 들여쓰기** (`src/render.py`, 결정적) — `-` 줄의 들여쓰기를 `+` 줄 전체에 붙이고 `+` 줄끼리의 상대 들여쓰기 유지.
  - 확인: 가짜 모델 테스트 `test_fix_plus_lines_take_indent_of_minus_line`.
- [x] **D2 인젝션 등급** (`src/guardrails.py`) — 리뷰 도구를 부르는 명령문만 security(medium), 따옴표·정규식 패턴 목록·탐지 주석 안의 문장과 부르는 말 없는 명령문은 "참고"(low, 꼭 고칠 것·요약에서 제외).
  - 확인: `test_case19_injection_stays_medium`, `test_quoted_or_pattern_example_is_low_note`. 실제 실행에서 19번 통과, day05 80행은 low 참고.
  - 기록: "AI에게"처럼 한글이 바로 붙으면 `\bAI\b`가 못 잡는 문제를 테스트가 찾아 영문자 경계로 고침.
- [x] **D3 E501 묶음과 요약 규칙** (`src/render.py`, `src/agent.py`) — 화면에서 파일별 E501을 한 항목으로(코드 줄·주석·docstring, ruff 길이, 수정안 없음, 나누는 방법·저장소 ruff 설정 안내 한 줄). 요약·추천은 보안·버그부터, 스타일 최대 1줄(코드로 확인, 1회 재생성 뒤 사실 문장으로 대체). Review JSON과 평가는 개별 지적 기준.
  - 확인: `test_line_length_findings_are_grouped_per_file`, `test_synth_payload_orders_groups_and_drops_notes`. 실제 실행 4·14·15·17·19번 통과(`v2-check3`).
  - 기록: 묶음에서 수정안을 빼자 4번 trait "줄을 나누는 방법 제안"이 실패 → 묶음 안내 줄에 일반적인 나누는 방법 한 문장 추가(줄마다 수정안은 여전히 없음).
  - 보완 (요약 구조): 요약 2·3·4줄은 코드(`secbug_line`·`style_line`·`scope_line`), LLM은 `SummaryDraft`(change·action·recommendations)만. 규칙 위반 줄은 1회 재생성 후 그 줄만 코드 문장(`change_fallback`·`action_fallback`), `trace`에 `stage="summary"`, `replaced`. `ReviewSummary`·스타일 줄 수 제한 함수는 삭제.
    - 확인: `test_code_summary_lines_and_llm_line_rules`, `test_llm_summary_lines_are_replaced_after_one_retry`, `test_run_query_returns_three_keys_and_trace`.
- [x] **D4 저장소 설정·변경 한도·줄 길이 지적**
  - D4-1 저장소 ruff 설정: `config.ReviewSettings`·`load_review_settings(repo_dir, max_lines)`, `run_ruff`·`build_tools`에 설정 전달, 적용 기준을 화면 맨 위 줄과 요약 4줄에 표시. 확인: `test_run_ruff_follows_repo_ruff_setting`.
  - D4-2 줄 길이 지적은 코드가 ruff 결과로 만듦(`line_length_findings`, 숫자는 ruff 값), 리뷰어 프롬프트에서 E501 제외, 리뷰어가 낸 E501은 버리고 개수를 trace에. 확인: `test_line_length_findings_are_made_by_code`.
  - D4-3 변경 한도: 기본 300, 최대 500(`config.MAX_LINES_CAP`), 사용자 지정 > 저장소 `[tool.citereview] max-lines` > 300. CLI `--repo-dir`·`--max-lines`, GUI 변경 한도 입력칸(커밋 목록 "초과" 표시가 적용 한도를 따름), 평가는 항상 기본값. `run_eval.py` summary에 요약 줄 대체 비율(`summary_replaced`). 확인: `test_review_settings_priority_and_cap`, `test_check_input_uses_max_lines`, `test_other_origin_is_rejected`(GUI).
  - D4-4 토큰 측정: Haiku 4.5 일일 토큰 한도로 중단(day03은 5회 호출 뒤 한도, day06은 0회). 리뷰 모델을 gpt-oss-120b로 바꾼 뒤 **D5 실행과 함께 측정**합니다(D6-3의 trace `usage`). day03(290줄, 기본값)과 day06(338줄, `--max-lines 500`)을 같이 실행하고, 500줄에 가까운 실제 diff가 있으면 실측, 없으면 두 점 선형 추정치로 리포트에 명시.
- [x] **D6 gpt-oss 교체 후 보완** (리뷰 모델 `openai.gpt-oss-120b-1:0`, 사용자 결정)
  - D6-0 교체 대응: `supports_tool_choice_values` 지정, 리뷰어 보완 단계(`structured`: agent/finalize/none), 종합의 빈 구조화 출력은 형식 오류, 한국어 지시와 한글 없는 요약 줄 규칙 위반, 추론 블록 마스킹. 확인: `test_reviewer_without_result_tool_is_finalized`.
  - D6-1 10번 오탐 조사: 3회 실행에서 재현 0회(앞선 1회 포함 4회 중 1회), 변경 후 파일은 `ast.parse` 통과, 리뷰어 입력 형식의 들여쓰기 열은 일정. 자료: `evaluation/notes/case10/`.
  - D6-2 파싱 실패 주장 검증(출력 검증 ④, `verify_syntax_claims`): SyntaxError·IndentationError·문법·구문 오류를 주장하는 지적만 대상, 파싱되면 제거(`syntax_dropped`), 일부만 보이거나 실제 실패면 유지(`syntax_kept`). 확인: `test_parse_failure_claim_dropped_but_logic_bug_kept`(논리 버그 유지), `test_parse_failure_claim_kept_when_broken_or_partial`.
  - D6-3 요약 줄 길이 숫자 검사: 줄 길이·E501·line-length를 언급한 문장만, 기준값은 요약 4줄에만, 종합 입력에 적용 기준. 확인: `test_length_numbers_checked_only_in_line_length_sentences`, `test_wrong_length_number_in_summary_is_replaced`.
  - D6-4 역할별 토큰: trace `usage`(보완·재생성 따로), `run_eval.py` summary에 `usage`·`structured`·`syntax`(④ 덕분에 통과한 케이스 수 포함). 확인: `test_usage_is_recorded_per_role_with_retry_and_finalize_separate`, `test_eval_summaries_for_usage_structured_and_syntax_rescue`, 실제 실행 1건.
  - D6-5 리뷰어 결과 없음 표시: `structured`가 none이면 맨 위 "주의: … 결과 없음(확인 못 함)", 빈 섹션은 "(확인 못 함)", "문제를 찾지 못했습니다." 생략. trace `stage="reviewers"`, `run_eval` summary `structured.none_by_reviewer`. 확인: `test_missing_reviewer_result_is_shown_as_unchecked_not_no_issues`. (04번 실제 실행에서 스타일 리뷰어 none 1회 발견이 계기)
  - D6-6 D5 준비: `run_eval.py --review-model gpt-oss|haiku`(판정 Nova Pro 고정), `--core`(2·3·4·14·15·17·19), 폴더 이름 끝에 모델 이름, `run_meta.json`·`summary.json`에 모델 기록, `round3_report.md` 틀(조건 A·B).
  - D6-7 더미 표시 비밀값(day04 `0c4f653` 리뷰가 계기): 주석에 더미 표시가 있으면 low 참고, 값 자체는 검사하지 않음(18번 high 유지, 평가셋 22개 diff에서 low로 바뀌는 비밀값 0건 — 조건 A·B 비교에 영향 없음). 확인: `test_example_in_value_only_stays_high`, `test_dummy_comment_above_lowers_to_note`.
  - D6-8 day04 버그 리뷰어 none 원인(LangSmith trace): 같은 diff를 두 번 리뷰, 두 번째에 버그 리뷰어가 결과 도구 대신 `{"findings": []}`를 글로 답하고(입력 7,405 토큰, stop=end_turn) 보완 단계도 글로만 답함(출력 12토큰) → 구조화 파서 None. 입력 크기보다 "JSON을 글로 답하는" 모델 습관이 원인. `run_eval` summary `structured_by_size`(케이스별 상태·.py 줄 수·리뷰어 입력 토큰) 추가.
- [ ] **D5 다음 버전 평가** (커밋 `00807f1`) — B 완료, **A 보류**(Haiku 일일 토큰 한도, `00807f1` worktree로 재실행 예정 — 절차는 round3_report.md 1절).
  - [x] B: gpt-oss 22건 — 본 세트 17/20(실패 4·9·10), 추가 1/2(실패 22), 실행 오류 0, structured none 2/51(JSON 글 응답). 커밋 재리뷰 5건(day07·day05·day03·day06 500·`cd65c0f` 409)과 토큰(500줄은 추정치). 리포트 `evaluation/round3_report.md`.
  - [x] README를 round3 기준으로 갱신(2차 값은 비교용으로 나란히, 리뷰 모델 표시).
  - [ ] A: Haiku 핵심 7건 → round3_report.md 1절·6절 결론만 갱신.
- [x] **round3 이후 ② JSON 글 응답 파싱** — 리뷰어 답·보완 답이 JSON 글이면 파싱(`agent_text`·`finalize_text`), 모두 실패할 때만 none. 확인: `test_reviewer_json_text_is_parsed_without_extra_call`, `test_finalize_json_text_is_parsed_and_only_garbage_is_none`. 종합 노드 글 응답(10번)은 범위 밖, 결정 대기.
- [x] **round3 이후 ③ 줄 길이 화면 간략화** — "## 참고" 두 줄, 상세는 --verbose, 건수·HITL 번호 제외, 추천 개선안 줄 길이 최대 1개, Review JSON은 그대로. 확인: `test_line_length_is_a_short_note_not_numbered_items` 외 3건, 4·14·15·17번 실제 실행(참고 줄로 판정 항목 통과, 17번은 2차 시도).
  - 기준 시점: 2차 제출은 태그 `submission-round2`(커밋 `7d2400c`), 다음 버전은 main (submission-round2 태그 이후).
