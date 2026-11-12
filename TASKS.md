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

- [ ] **G1 GUI 1단계: 기능** (남은 순서 6) — Hallmark 설치(Node 확인, `-g` 없이 이 프로젝트에만, puppeteer·Chrome이 없으면 정적 검사만), `src/gui_server.py`(`http.server`, 127.0.0.1)와 `run.bat`. 단순 HTML로 ① 저장소 폴더 → 커밋 목록(`.py` 변경 줄 수, 300줄 초과 표시) 선택 또는 diff 파일 선택 ② 리뷰 실행(별도 스레드)·trace 노드 진입 폴링으로 진행 표시 ③ 렌더러 결과를 고정폭으로 표시 ④ 승인/거절 버튼(`resume_review`, dry-run + 허용 목록). Hallmark 설치 경로와 `.gitignore` 처리를 CLAUDE.md 2번에 확정해 적음.
  - 확인 **(실제 LLM)**: 03번 diff 리뷰 결과가 화면에 CLI와 같은 형식으로 나오고, 19번 diff는 승인 대기에서 멈춘 뒤 허용 목록 밖 저장소로 승인하면 차단 메시지가 나온다.
- [ ] **G2 GUI 2단계: 디자인** (남은 순서 7) — Hallmark custom 또는 차분한 테마 하나로 화면 정리(테마 비교 생략). G1의 버튼·입력·핸들러는 그대로 유지. 히어로·마케팅 문구·구독 폼·페이지 끝 CTA 없음.
  - 확인: G1의 확인 두 가지가 디자인 적용 뒤에도 그대로 되고, 결과 영역의 줄 번호·`>`·`-`/`+` 정렬이 CLI 출력과 같다.
- [ ] **G3 시연 시나리오 확인** (남은 순서 8) — GUI가 완성되면 docs/plan.md 2절 시연 시나리오에 GUI 장면을 넣을지 확인.
  - 확인: 넣기로 하면 시연 표에 GUI 장면과 확인 포인트가 있고, 넣지 않기로 하면 그 사유가 docs/plan.md에 한 줄 있다.

- [x] **(선택) C2 PEP 8 근거 ruff 교차 확인** (B5 준비에서 2차 전에 적용. 출력 검증 순서는 존재 검사 → C2, 가짜 모델 테스트 1건, 2차 실제 실행 제거 0건) — 출력 검증에서 `PEP8-*` 근거를 단 style 지적은 그 줄에 연결표상 대응하는 ruff 위반이 있을 때만 유지(근거-지적 일치율과 같은 검사를 출력 가드레일로). **2차 평가 전에 할지는 남은 순서 1번(B5 준비)에서 정합니다.** 하면 2차 리포트에 "에이전트 개선"으로 적습니다.
  - 확인: 가짜 모델 테스트 1건 — 대응 ruff 위반이 없는 줄에 `PEP8-maximum-line-length`를 단 style 지적은 제거되고, `ruff:E501`이 있는 줄의 같은 지적은 남는다. 연결표에 없는 PEP 8 섹션을 인용한 지적은 제거하지 않고 그대로 둔다(판정 불가).
- [ ] **(선택) C1 PR 조회** — `github_get_pr`(MCP)로 PR URL 입력 지원.
  - 메모: 이번 제출 범위에서는 하지 않음. 쓰지 않는 의존성 `langchain-mcp-adapters`는 `requirements.txt`에서 뺐으므로, 진행하면 다시 추가합니다.
  - 확인 **(실제 연결)**: 본인 테스트 저장소 PR URL로 `review`가 끝까지 돈다.
