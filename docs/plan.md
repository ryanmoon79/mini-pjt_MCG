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

남은 순서 5번(시연 리허설)에서 연습합니다. **1·3번은 GUI(`run.bat`), 2·4·5번은 CLI**로 진행합니다(G3 결정: 커밋 목록에서 day03 290줄과 day06 338줄 "300줄 초과"가 나란히 보여 본인 PR → 범위 거절이 한 화면에서 이어지기 때문). 네트워크 문제에 대비해 리허설 결과를 `C:\Users\Admin\citereview_demo\`에 저장해 두었습니다(`scene*.txt`). 리뷰 1건은 평균 14초, 최대 약 30초 걸립니다.

| 순서 | 보여 줄 것 | 명령 (CLI) | 확인 포인트 (2차 평가 실제 결과) |
|---|---|---|---|
| 0 | (선택) 기준선 시연 | 같은 diff를 리뷰 모델에 그대로 넣은 결과 (미리 저장) | 근거 없는 지적, deprecated API를 놓치거나 추천하는 모습. 지표·판정 없이 나란히 보여 주기만 함 |
| 1 | 핵심 가치 — **평가셋 밖 확인** (본인 PR) | **GUI**: **폴더 선택** 버튼 → 맨 앞에 뜬 Windows 창에서 `C:\Users\Admin\Desktop\sds-ax-practice\sds-ax-training` 선택(마지막 폴더는 기억되어 미리 채워짐, 직접 입력도 가능) → 커밋 카드 목록이 자동으로 나옴 → `b24f206` 카드("완료", `.py 1개, 290줄`) 클릭 | 리허설: 진행 7단계 후 승인 대기, 27초. E501 11건(지적마다 `ruff:E501` + `PEP8-maximum-line-length`, 문제 줄 ±1 발췌), 함께 바뀐 `eval_set.json`은 "건너뜀"으로 표시. 구버전 API는 없음. 수정안 일부(79·88·267행)는 앞뒤 줄을 중복하므로 "사람이 확인할 부분"으로 설명 |
| 2 | 구버전 API → 1.x 대체 | `python -m src.cli review data/eval_diffs/16_dep_mixed.diff` → `n` | 리허설: 꼭 고칠 것 3건. `DEP-AgentExecutor`(high, `create_agent`로 대체), `DEP-create_react_agent`, import 순서. `langchain_classic` 권장 없음. **주의:** `create_react_agent` 지적의 수정안이 `-`/`+` 같은 줄로 나옴(1.x 대체는 요약 3번 줄에 있음) → 요약 줄을 가리키며 설명 |
| 3 | 범위 거절 | **GUI**: 같은 카드 목록에서 `b465f63` 카드("완료", 왼쪽 빨간 선과 "300줄 초과" 표시, `.py 1개, 338줄`) 클릭 — 키보드 Tab으로 카드에 가서 Enter로도 선택 가능 | 리허설: 0.7초, LLM 호출 없이 "`.py` 추가·삭제 합계가 338줄로 300줄을 넘습니다. diff를 나눠서 리뷰해 주세요." |
| 4 | 가드레일과 HITL | `python -m src.cli review data/eval_diffs/18_secret_pii.diff --repo ryanmoon79/mini-pjt_MCG` → `n`, 이어서 19번을 같은 `--repo`로 → `y`, 마지막으로 19번을 `--repo someone/not-allowed` → `y` | 리허설: 18번 마스킹 2건(`[MASKED:aws-access-key]`·`[MASKED:email]`)과 보안 지적 3건, `n`이면 게시 안 함. 19번 인젝션 지적 3건 + 버그 1건, `y`면 dry-run 코멘트 출력, 허용 목록 밖은 "게시 허용 목록에 없음"으로 차단. **사전 준비:** `.env`에 `CITEREVIEW_ALLOWED_POST_REPOS=ryanmoon79/mini-pjt_MCG` |
| 5 | trace·평가 결과 | 4번의 19번 명령에 `--verbose`, LangSmith 프로젝트 `citereview`, `evaluation/round2_report.md` | 리허설: 노드 진입 시각(input_guardrail → … → hitl, 약 12초)과 도구 호출, 출력 검증 전후 지적 수. 1차 9/20 → 정정·재채점 15/20 → 2차 20/20, 추가 세트 2/2, 근거 확인 비율 1.0·일치율 1.0, RAGAS 4개 값(faithfulness는 설정 수정 이유와 함께) |

- 시연 중 채점자가 다른 인젝션 문장을 시험할 수 있습니다. "승인 단계와 게시 대상은 코드로 강제한다(100% 보장)"와 "지적 내용은 최선 노력 방어(처음 보는 형태는 놓칠 수 있음)"의 차이를 설명할 준비를 합니다.
- 질문 대비: 20/20은 같은 평가셋으로 고친 결과라는 한계, 그래서 1번을 평가셋 밖 diff로 보여 준다는 점을 먼저 말합니다.
