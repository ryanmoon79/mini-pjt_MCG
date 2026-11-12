# 작업 순서와 시연 계획 · CiteReview

> 작업 내용은 [TASKS.md](../TASKS.md), 개발 규칙은 [CLAUDE.md](../CLAUDE.md), 평가 규칙은 [evaluation/README.md](../evaluation/README.md)를 보세요. 이 문서는 작업 순서·판단 시점·포기 순서만 다룹니다.

## 1. 작업 순서와 판단 시점

**지금까지 끝난 것:** A1~A5, B1~B5, C2, 1차 평가(9/20, 정정·재채점 15/20), 2차 평가(20/20, 추가 세트 2/2)와 RAGAS. 2차 결과가 제출 코드 기준이므로 이후 에이전트 코드는 고치지 않습니다. 관문 push(A6)와 제출 커밋 시점은 사용자가 정합니다.

**남은 순서 — 필수 작업 먼저, GUI는 그 뒤**

1. B5 준비: 실행 기록에 RAGAS reference용 청크 원문 저장, `run_ragas.py` 작성, **21·22번 diff 작성, 근거-지적 일치율 계산 추가** (2차 평가 전에 필요). 선택 작업 C2(PEP 8 근거 ruff 교차 확인)를 2차 전에 할지 여기서 정함
2. 2차 평가 (본 세트 20건 × 1회 + 추가 세트 21·22, 동시 실행 2개)
3. RAGAS 4개 지표 → `round2_report.md`
4. README 완성 (B6)
5. 시연 리허설 (2절), 결과 화면 저장
6. GUI 1단계: 기능 (TASKS G1)
7. GUI 2단계: Hallmark 디자인 (TASKS G2)
8. GUI가 완성되면 시연 시나리오(2절)에 GUI 장면을 넣을지 확인

그 뒤 여유가 있으면 선택 작업 C1(PR 조회)을 하고, 마지막에 제출 커밋 `[제출] MiniPJT`를 합니다.

**판단 시점**
- **최소 경로 완료 후 판단** (통과함): 평가 diff 3개가 최소 경로를 평가 실행까지 통과했는가 → 관문 push. 안 됐으면 그 상태로 push하고 다음 작업 전에 포기 순서를 적용합니다.
- **2차 평가:** 개선이 다 끝나지 않았어도 순서가 오면 돌립니다.
- **GUI 진행 조건:** 필수 작업(1~5: B5 준비, 2차 평가, RAGAS, README, 리허설)이 끝난 뒤에만 GUI(6~8)를 진행하고, 과제 평가 전에 멈춥니다.

- **포기 순서 (일정이 밀리면, 앞에서부터):** ① GUI 2단계(Hallmark 디자인) → ② GUI 1단계 → ③ PR 조회(MCP, TASKS C1) → ④ PEP 8 근거 ruff 교차 확인(TASKS C2) → ⑤ 기준선 시연(2절 0단계)
  - C2는 GUI·PR 조회보다 늦게 포기합니다(평가 품질에 직접 닿는 작업이라서).
  - 추가 세트 21·22번과 근거-지적 일치율은 B5에 들어 있어 포기 대상이 아닙니다.
- **절대 포기하지 않는 것:** 평가 CSV 20건, 1·2차 평가 리포트, guardrail 3/3, `tests/` 2건, RAGAS 4개 지표, README
  - RAGAS는 `faithfulness`·`answer_relevancy`를 먼저 계산합니다. `context_precision`·`context_recall`은 reference 구성이 실패하거나 일정이 밀리면 리포트에 "미측정 + 사유"로 적습니다(포기가 아니라 결과 기록).
- **의도적 비목표:** Java 실제 구현, 배포용 웹 서비스(시연용 로컬 GUI는 범위 안), 추가·삭제 300줄을 넘는 diff, LangChain 0.x 코드베이스 리뷰, 실제 PR 코멘트 게시, 그 밖에 범위에서 뺀 항목 (README "트라이앤에러 회고 → 남은 한계"의 다음 단계 후보)

## 2. 라이브 시연 시나리오 (day08 안내 "자동채점과 라이브 시연")

남은 순서 5번(시연 리허설)에서 연습합니다. **CLI 기준**이며, GUI는 완성되면 장면을 추가합니다(남은 순서 8번). 시연용 diff는 미리 준비하고, 네트워크 문제에 대비해 결과 화면을 `--batch` 출력과 터미널 캡처로 저장해 둡니다. 리뷰 1건은 2차 평가 기준 평균 14초, 최대 21초 걸립니다.

| 순서 | 보여 줄 것 | 명령 (CLI) | 확인 포인트 (2차 평가 실제 결과) |
|---|---|---|---|
| 0 | (선택) 기준선 시연 | 같은 diff를 리뷰 모델에 그대로 넣은 결과 (미리 저장) | 근거 없는 지적, deprecated API를 놓치거나 추천하는 모습. 지표·판정 없이 나란히 보여 주기만 함 |
| 1 | 핵심 가치 — **평가셋 밖 확인** (본인 PR) | `python -m src.cli review C:\Users\Admin\citereview_demo\day03_b24f206.diff` (실습 저장소 day03 "완료" 커밋, `.py` 290줄, LangGraph 1.x, 저장소 밖에 저장) | 리허설 결과: E501 11건, 지적마다 `ruff:E501` + `PEP8-maximum-line-length` 근거와 문제 줄 ±1 발췌, 근거 확인 비율 1.0, C2 제거 0. 구버전 API는 없음. 고치는 방법 일부(1·2·8번)는 앞뒤 줄을 중복하므로 그대로 따르지 말라고 설명 |
| 2 | 구버전 API → 1.x 대체 | `python -m src.cli review data/eval_diffs/16_dep_mixed.diff` | `DEP-create_react_agent`·`DEP-AgentExecutor` 근거와 1.x 대체 코드, `langchain_classic` 권장 없음 (2차 통과 케이스) |
| 3 | 범위 거절 | `python -m src.cli review C:\Users\Admin\citereview_demo\day06_b465f63.diff` | 실습 저장소 day06 "완료" 커밋(338줄) → LLM 호출 없이 "`.py` 추가·삭제 합계 338줄로 300줄을 넘습니다. diff를 나눠서 리뷰해 주세요." |
| 4 | 가드레일과 HITL | `python -m src.cli review data/eval_diffs/18_secret_pii.diff --repo <허용 저장소>` → `n`, 이어서 19번 → `y` | 18번: `[MASKED:aws-access-key]`·`[MASKED:email]` 표시와 security 지적. 19번: 인젝션 지적 3건 + 원래 버그 유지, 승인 전에는 게시 없음 → `y` 후 dry-run 코멘트 출력, 허용 목록 밖 저장소는 거부 |
| 5 | trace·평가 결과 | 같은 명령에 `--verbose`, LangSmith 프로젝트 `citereview`, `evaluation/round2_report.md` | 노드·도구 호출 흐름과 출력 검증 전후 지적 수. 1차 9/20 → 정정·재채점 15/20 → 2차 20/20, 추가 세트 2/2, 근거 확인 비율 1.0·일치율 1.0, RAGAS 4개 값(faithfulness는 설정 수정 이유와 함께) |

- 시연 중 채점자가 다른 인젝션 문장을 시험할 수 있습니다. "승인 단계와 게시 대상은 코드로 강제한다(100% 보장)"와 "지적 내용은 최선 노력 방어(처음 보는 형태는 놓칠 수 있음)"의 차이를 설명할 준비를 합니다.
- 질문 대비: 20/20은 같은 평가셋으로 고친 결과라는 한계, 그래서 1번을 평가셋 밖 diff로 보여 준다는 점을 먼저 말합니다.
