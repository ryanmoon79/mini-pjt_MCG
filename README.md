# 미니 PJT: CiteReview — 근거를 다는 코드 리뷰 에이전트

## 무엇을 푸나

LLM 코드 리뷰는 근거가 없어 믿고 고칠지 판단하기 어렵고, LangChain 0.x → 1.x 전환기에는 이미 제거된 API를 놓치거나 오히려 추천하기 쉽습니다.

**CiteReview는 LangChain/LangGraph 1.x로 실습하는 수강생의 PR diff를 받아 요약 5줄·잠재 버그·스타일 이슈·추천 개선안을 내놓고, 모든 지적에 확인할 수 있는 근거를 붙입니다.**

- **근거 3종:** PEP 8 섹션·deprecated API 매핑표(문서), ruff·비밀값 탐지(도구), diff의 줄 위치(코드). 이번 리뷰에서 실제로 확인되지 않는 근거는 출력 전에 코드가 지웁니다.
- **구버전 API 탐지:** 설치된 1.x 기준 매핑표로 `LLMChain`·`create_react_agent` 같은 잔재를 찾고 1.x 대체 코드를 제안합니다. `langchain_classic` 권장은 코드가 결정적으로 막습니다.
- **가드레일:** 비밀값·개인정보는 LLM 호출 전에 마스킹하고, diff 안의 지시문(프롬프트 인젝션)은 따르지 않으며, 게시는 사람 승인 후에만 합니다(이번 범위는 dry-run).
- 자세한 기획은 [SERVICE.md](SERVICE.md)에 있습니다.

### 핵심 결과 한눈에

> **두 기준을 나란히 적습니다.** 2차는 제출 시점(태그 `submission-round2`, 리뷰 모델 Claude Haiku 4.5), 3차 B는 다음 버전 코드(커밋 `00807f1`)를 gpt-oss-120b로 돌린 결과입니다. **리뷰 모델과 코드가 함께 바뀌어 직접 비교가 아닙니다.** 같은 Haiku로 다음 버전 코드를 돌리는 3차 A는 토큰 한도로 보류 중입니다. 자세한 원인 분석은 [round3_report.md](evaluation/round3_report.md).

| 항목 | 2차 (Haiku, 제출) | 3차 B (gpt-oss, 다음 버전) |
|---|---|---|
| 인-아웃 통과율 (본 세트 20건) | 1차 **9/20** → 정정·재채점 **15/20** → 2차 **20/20** (완성 기준 14/20) | **17/20** (실패 4·9·10) |
| 추가 세트 (함정 케이스 21·22) | **2/2** | **1/2** (실패 22) |
| 근거 확인 비율 (존재) | **1.0** (31 → 31) | **1.0** (31 → 31) |
| 근거-지적 일치율 (타당) | **1.0** (8/8), C2 제거 0건 | **1.0** (4/4), C2 제거 0건 |
| 전체 실행 시간 (22건, 동시 2개) | 1차 4분 31초 (20건, 동시 5개) → 2차 **2분 55초** | **2분 37초** |
| 케이스당 토큰 (평균) | (기록 없음) | 입력 13,694 · 출력 1,626 · 호출 8회 |
| RAGAS | faithfulness 0.729 · answer_relevancy 0.264 · context_precision 0.711 · context_recall 1.000 ([아래](#ragas-평가-결과)) | 미실행 |

## 활용한 패턴 (Day 1–7)

과정 공식 패턴 12개 중 **11개 적용** (MCP는 선택 작업 C1으로 미구현).

| # | 패턴 | Day | CiteReview에서 쓰는 곳 |
|---|---|---|---|
| 1 | LCEL chain (Pydantic 구조화 출력) | Day 1 | 종합 노드: `SYNTH_PROMPT \| llm.with_structured_output(ReviewSummary)`로 요약 5줄·추천 개선안 생성 (형식 오류 시 1회 재생성) |
| 2 | ReAct (도구 자율 선택) | Day 3 | 리뷰어 2종이 `create_agent`로 `ruff_check`·`search_evidence` 중 필요한 도구를 스스로 고름 |
| 3 | RAG (하이브리드 검색) | Day 2 | `retriever.py`: PEP 8·deprecated 매핑표 12청크(`rule_id` 메타데이터), **BM25 + Chroma 벡터를 RRF로 직접 합침** (`EnsembleRetriever`는 1.x에서 `langchain_classic`으로 옮겨져 쓰지 않음) |
| 4 | 도구 다중 | Day 4 | 한 리뷰에서 `ruff_check` → `search_evidence`(ruff 결과의 `pep8_rule_id`로 PEP 8 섹션 검색)를 이어서 사용 |
| 5 | MCP 서버 연동 | Day 4 | **미구현** — GitHub MCP PR 조회(`github_get_pr`)는 선택 작업 C1 |
| 6 | 가드레일 | Day 5 | 입력: 비밀값·PII 마스킹, 인젝션 탐지, 범위(`.py`만, 300줄) / 출력: 스키마, 근거 실재 확인, `langchain_classic` 권장 제거, PEP 8 근거 ruff 교차 확인(C2) |
| 7 | HITL | Day 5 | 게시 노드 앞 `interrupt()`로 승인·거절·항목별 제외 (`InMemorySaver`로 같은 프로세스 안에서 재개, 게시는 dry-run + 허용 목록) |
| 8 | 미들웨어 | Day 5 | 모든 리뷰어에 모델 호출 직전 마스킹 미들웨어(`wrap_model_call`) + `ModelCallLimitMiddleware` |
| 9 | Multi-Agent Supervisor | Day 6 | `langgraph_supervisor.create_supervisor` + 버그·스타일 리뷰어 (supervisor가 계획된 리뷰어를 건너뛰면 코드가 직접 실행) |
| 10 | Plan-Execute | Day 7 | 규칙 기반 planner: 버그 리뷰어는 항상, 스타일 리뷰어는 추가된 줄이 있을 때 |
| 11 | Observability · Trace | Day 7 | `run_query`의 `trace`(시각 포함 결정적 이벤트: 노드·도구 호출·가드레일 전후 지적 수·HITL), 평가 기록 `trace.json`, `.env`에 LangSmith 키가 있으면 LangSmith |
| 12 | 평가 (RAGAS · LLM-as-Judge) | Day 7 | RAGAS 4개 지표, Nova Pro로 문장형 항목 판정(코드로 판정할 수 있는 항목은 코드로), 1·2차 자체 평가 |

## 아키텍처

```
diff 파일
 → [입력 가드레일]  비밀값·PII 마스킹, 인젝션 탐지(security 지적), .py만 남기고 300줄 초과·.py 없음은 거절   (LLM 없음)
 → [planner]        리뷰어와 파일 범위 결정                                                            (LLM 없음)
 → [supervisor]     버그 리뷰어 / 스타일 리뷰어  (create_agent + ruff_check·search_evidence, 마스킹·호출 제한 미들웨어)
 → [출력 검증]      ① 근거 존재 검사(doc은 이번 검색 결과, tool은 이번 도구 출력, code는 diff의 줄)
                    ② C2: PEP 8 근거는 그 줄에 대응 ruff 위반이 있을 때만 유지
                    ③ langchain_classic 권장 문장 제거(재생성 1회 → 결정적 대체)                    (LLM 없음)
 → [종합]           LCEL로 요약 5줄 + 추천 개선안
 → [렌더링]         터미널 Markdown (코드 발췌는 diff에서 결정적으로)                                   (LLM 없음)
 → [HITL]           interrupt로 승인 대기 (평가·--batch는 여기서 멈춤)
 → [게시]           승인 + 허용 목록 저장소일 때만 dry-run 출력                                         (LLM 없음)
```

LLM은 리뷰어·supervisor·종합 노드에만 쓰고, 계산과 규칙 판정(줄 수, 근거 실재, 승인·게시 대상)은 모두 파이썬 코드가 합니다.

## 실행 방법

**1. 환경** (Python 3.12 이상, 과정 venv에서 확인)

```bash
pip install -r requirements.txt
```

프로젝트 루트에 `.env`를 만들고 Bedrock 접근 키를 넣습니다 (커밋하지 않음).

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
CITEREVIEW_ALLOWED_POST_REPOS=owner/repo   # (선택) 게시 허용 저장소, 쉼표 구분
```

**2. 리뷰** (처음 실행할 때 코퍼스 12청크를 `./chroma_db`에 색인합니다)

```bash
git diff -U9999 > my.diff                                            # 리뷰할 변경을 diff로 저장
python -m src.cli review my.diff                                     # 리뷰 출력 → 게시 승인 여부 질문
python -m src.cli review data/eval_diffs/03_llmchain.diff --verbose  # 내부 로그 포함
python -m src.cli review my.diff --batch                             # 승인 대기에서 멈추고 결과 JSON 출력
python -m src.cli review my.diff --repo-dir ../my-repo --max-lines 500  # 저장소 ruff 설정 + 변경 한도 500
```

변경 한도는 기본 300줄이고 `--max-lines`(최대 500) > 저장소 `pyproject.toml [tool.citereview] max-lines` > 300 순서로 정합니다. `--repo-dir`를 주면 그 저장소의 ruff 설정(`ruff.toml`·`.ruff.toml`·`pyproject.toml [tool.ruff]`)으로 검사합니다. 적용된 기준은 결과 맨 위 줄과 요약 4줄에 보입니다.

승인 질문에는 `y`(승인), `n`(거절), `i 2 3`(2·3번 항목을 빼고 승인)으로 답합니다. 게시는 dry-run이라 코멘트 내용만 출력합니다.

**3. 테스트** (가짜 모델만 써서 실제 API를 호출하지 않음)

```bash
python -m pytest tests -q
```

**4. 평가** — 결과는 `evaluation/runs/<실행ID>/`에 저장 (커밋 제외)

```bash
python evaluation/validate_test_queries.py           # CSV 형식·건수 검증 (모델 호출 없음)
python evaluation/run_eval.py                        # 본 세트 20건 + 추가 세트 2건 (동시 실행 2개)
python evaluation/run_eval.py --cases 3,4,10         # 일부 케이스
python evaluation/run_eval.py --rejudge <실행ID>     # 저장된 기록을 현재 판정기로 다시 채점 (리뷰 모델 호출 없음)
```

**5. RAGAS** — ragas 의존성(`scikit-network`)이 Python 3.14 휠을 제공하지 않아 Python 3.12 평가 전용 환경을 따로 씁니다. `run_ragas.py`는 `src/`를 import하지 않고 실행 기록만 읽습니다.

```bash
uv venv .venv-eval --python 3.12
uv pip install --python .venv-eval/Scripts/python.exe -r requirements-eval.txt
.venv-eval/Scripts/python.exe evaluation/run_ragas.py <실행ID>
```

**6. 시연용 로컬 GUI** — 표준 라이브러리 `http.server`로 `127.0.0.1:8765`에서만 열립니다. CLI와 같은 `run_query`·`resume_review`를 씁니다.

```bash
python -m src.gui_server      # 브라우저가 열림 (Windows는 run.bat 더블클릭)
```

**폴더 선택** 버튼(맨 앞에 뜨는 Windows 폴더 창)이나 경로 입력으로 저장소를 고르면 커밋 카드 목록(메시지, 해시, 날짜, `.py` 변경 줄 수, 적용 한도 초과 표시)이 나오고 카드를 눌러 리뷰합니다. 변경 한도 입력칸(비우면 저장소 설정 또는 300, 최대 500)을 바꾸면 초과 표시가 새 한도로 다시 계산되고, 커밋 리뷰는 그 저장소의 ruff 설정을 따릅니다. 또는 diff 파일을 고르면, 진행 단계(입력 가드레일 → planner → 리뷰어 → 출력 검증 → 종합 → 렌더링 → 승인 대기)와 CLI와 같은 고정폭 결과가 나오고, 승인/거절 버튼으로 dry-run 게시를 결정합니다.

## RAGAS 평가 결과

2차 평가 실행 기록(본 세트)으로 계산했습니다. 판정 LLM은 리뷰 모델과 다른 Nova Pro, 임베딩은 Titan v2입니다.

| 지표 | 값 | 대상 건수 |
|---|---|---|
| context_recall | **1.000** | 9 (traits에 문서 근거 ID가 있는 케이스) |
| context_precision | **0.711** | 9 |
| faithfulness | **0.729** | 19 (리뷰가 끝난 케이스, Java 거절 1건 제외) |
| answer_relevancy | **0.264** | 10 (검색 청크가 있는 케이스) |

- **faithfulness 설정:** contexts에 검색 청크뿐 아니라 마스킹된 diff hunk와 리뷰 중 ruff 출력을 넣었습니다. 리뷰는 diff와 도구 출력을 근거로 쓰는데, 검색 청크만 넣으면 diff에 대한 문장이 모두 "근거 없음"이 되기 때문입니다(검색 청크만: 0.466).
- **context_precision:** 9건 모두 정답 청크가 검색 순위 1위였지만, 14·17번을 판정 모델이 0.0으로 매겼습니다. **answer_relevancy**는 질의가 "…diff 를 리뷰해줘" 같은 짧은 명령이라 구조적으로 낮게 나옵니다.
- 자세한 해석과 케이스별 값은 [evaluation/round2_report.md 7절](evaluation/round2_report.md#7-ragas-4개-지표-본-세트)에 있습니다.

## 인-아웃 세트 통과율 (자체 평가)

평가셋: [evaluation/test_queries.csv](evaluation/test_queries.csv) 본 세트 20건(positive 8 / negative 4 / edge 5 / guardrail 3) + 추가 세트 2건. 리뷰 모델 1·2차 Claude Haiku 4.5, 3차 B gpt-oss-120b, 케이스마다 1회. 규칙은 [evaluation/README.md](evaluation/README.md)를 따릅니다.

- **1차:** 9 / 20 통과 → 같은 기록을 판정기 v2와 정정된 CSV로 재채점하면 15 / 20 ([round1_report.md](evaluation/round1_report.md))
- **2차 (개선 후):** **20 / 20** 통과, 추가 세트 2 / 2, 실행 오류 0건 ([round2_report.md](evaluation/round2_report.md))
- **개선폭: +11건**
  - 9 → 15 (+6): 판정기 오판 5건 해소, CSV 13번 정정(AttributeError → TypeError). 에이전트 출력은 그대로
  - 15 → 20 (+5): 호출 한도로 끝나지 못한 4건(재시도·호출 한도와 형식 오류 분리·동시 실행 2개), `langchain_classic` 권장 문장 1건(결정적 검사)
- 그 밖에: 근거 확인 비율 1.0, 근거-지적 일치율 1.0(8/8), guardrail 3/3, 원문 노출·무단 게시 0건, `tests/` 34건 통과
- **3차 B (다음 버전 코드 `00807f1` + gpt-oss-120b, 2차와 직접 비교 아님):** **17 / 20**, 추가 세트 1 / 2, 실행 오류 0건 ([round3_report.md](evaluation/round3_report.md))
  - 실패 원인: 4번은 **코드**(줄 길이 지적을 코드가 만들면서 리뷰어가 `search_evidence`를 부르지 않음 — CSV `expected_tools`와 불일치), 9·22번은 **모델**의 과잉 지적, 10번은 종합 노드가 구조화 출력 대신 글로 답한 **모델** 응답 형식(코드에 대비 없음)
  - 리뷰어 결과를 받지 못한 경우(`structured` none): 리뷰어 실행 51회 중 2회, 원인은 결과를 JSON 글로 답하는 gpt-oss 습관(크기와 무관해 보임)
  - 3차 A(같은 코드 + Haiku 4.5, 핵심 7건)는 토큰 한도로 보류, `00807f1` 코드로 재실행 예정

## 트라이앤에러 회고

### 시도했지만 실패한 접근
- **리뷰 모델 Sonnet 4.5 (과정 고정값):** Bedrock 계정 일일 토큰 한도(`Too many tokens per day`)로 개발이 막혀 Haiku 4.5로 교체했습니다(사용자 결정). 1·2차 모두 같은 모델입니다.
- **현재 기본 리뷰 모델 Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`, Bedrock `us-west-1`):** gpt-oss로 3차 평가를 마친 뒤, 토큰 한도 문제로 다시 바꿨습니다(사용자 결정). 리전이 다른 모델이 생겨 모델마다 리전을 따로 정하게 했고(`config.REVIEW_MODEL_REGIONS`), 호출 권한은 `.env`의 AWS 키에 따라 달라 키를 바꿔야 했습니다. gpt-oss·Haiku는 `--review-model`로 계속 고를 수 있습니다.
- **다음 버전 리뷰 모델 gpt-oss-120b (3차 조건 B):** Haiku 4.5도 일일 토큰 한도에 걸려 D4-4 토큰 측정이 중간에 끊겼고, `openai.gpt-oss-120b-1:0`(Bedrock `us-east-1`)으로 다시 바꿨습니다(사용자 결정). 바꾸면서 생긴 차이: ① `create_agent`의 구조화 출력이 넘기는 `tool_choice="any"`를 langchain_aws가 거절 → 모델 생성 시 지원 값을 직접 적음(Bedrock 서버는 받아 줌). ② 그래도 강제력이 없어 리뷰어가 결과 도구를 건너뛰는 경우가 있음 → 대화 기록으로 한 번 더 묻는 보완 단계와 trace `structured`(agent/finalize/none). ③ 종합 노드의 빈 구조화 출력은 형식 오류로 처리. ④ 영어로 답하는 경우가 있어 프롬프트에 한국어 지시, 요약 LLM 줄은 한글이 없으면 규칙 위반으로 처리. 1·2차 결과(Haiku)와 다음 버전 평가는 모델이 달라 직접 비교할 때 이 점을 함께 적습니다.
- **판정 LLM에 문장형 항목을 통째로 맡김:** 1차 실패 11건 중 5건이 판정기 오판(항목 누락, 답변에 있는 내용을 "없음"으로 판정)이었습니다. → 판정기 v2(번호로 주고받기, 누락 시 재요청, 형식이 정해진 항목은 코드 판정)로 바꾸고, **오판이었던 케이스와 원래 실패해야 하는 케이스로 판정기를 검증**한 뒤 재채점했습니다.
- **`langchain_classic` 권장을 프롬프트로만 막기:** 코드 블록 검사만으로는 설명 문장 속 권장(2번)을 놓쳤고, 재생성을 시키자 이번에는 `AgentExecutor`를 제안했습니다. → 지적 설명·고치는 방법·요약·추천을 **결정적으로 검사**하고, 재생성 1회에 매핑표를 주며, 그래도 남으면 깨끗한 코드는 살리고 문장만 지우는 대체로 바꿨습니다(요약은 5줄 유지).
- **동시 실행 5개:** 호출 한도로 4건이 끝나지 못했습니다. → 2개로 낮추고 재시도를 강화하니 오류 0건, 전체 시간도 4분 31초 → 2분 55초로 줄었습니다.
- **RAGAS faithfulness를 검색 청크만으로 계산:** diff에 대한 문장이 모두 근거 없음으로 잡혀 0.466. → 측정 설정을 원래 설계(검색 청크 + diff + ruff 출력)로 고쳐 0.729. 에이전트는 다시 돌리지 않았습니다.

### 최종 채택한 접근
- **근거는 코드가 검증:** 근거의 실재(존재 검사)와 승인·게시 대상은 LLM 출력과 관계없이 그래프 코드가 결정합니다.
- **중간 피드백 "근거가 존재하는 것과 지적이 맞는 것은 별개":** 함정 케이스 추가 세트(21·22), 출력 검증 전 지적으로 계산하는 근거-지적 일치율, PEP 8 근거를 ruff 위반과 맞춰 보는 C2를 넣었습니다. 2차에서는 리뷰어가 함정에 걸리지 않아 C2 제거 0건이며, C2 동작은 가짜 모델 테스트로 확인했습니다.
- **하루 범위로의 단순화:** 보안 리뷰어를 따로 두지 않고(정규식 + 버그 리뷰어), 체크포인터는 같은 프로세스 재개용 `InMemorySaver`, 게시는 dry-run, 평가는 케이스마다 1회, 코퍼스는 PEP 8 5개 섹션 + 매핑표로 줄였습니다.
- **RAGAS는 Python 3.12 평가 전용 환경:** 과정 venv(Python 3.14)에서 `scikit-network` 휠이 없어 설치가 실패했기 때문입니다.

### 남은 한계
- **같은 평가셋으로 실패를 보고 고쳤습니다.** 2차 20/20은 처음 보는 코드에서의 성능을 보장하지 않습니다. 평가셋 밖 확인은 추가 세트 21·22(2/2)와 본인 실습 저장소 PR 시연뿐이라 건수가 적습니다.
- **문제없는 diff에서 요약이 근거 없는 평가성 문장을 씁니다**(11번 faithfulness 0.294). 2차 결과를 제출 코드 기준으로 두려고 이번에는 고치지 않았습니다.
- **승인 안내 문구:** 무단 게시는 코드로 막지만, 답변이 "게시는 승인 후에만 가능하다"고 안내하지는 않습니다(20번 보조 항목).
- **프롬프트 인젝션:** 아래 3개 패턴(한·영)을 정규식으로 탐지해 security 지적을 남기고, diff는 구분자로 감싸 데이터로만 넘깁니다. **처음 보는 형태의 지시문은 막지 못할 수 있습니다.** 다만 승인 단계와 게시 대상은 코드로 강제하므로 어떤 지시문도 무단 게시로 이어지지 않습니다.
  - (다음 버전) 리뷰 도구를 부르는 말("리뷰 도구에게", AI 등)이 있는 명령문만 security(medium)로 잡고, 따옴표·정규식 패턴 목록·탐지 주석 안의 문장은 "참고: 인젝션 예시 문장 포함"(low)으로 낮춥니다. 그래서 **따옴표로 감싼 공격 문장은 low로만 잡히고, 리뷰 도구를 부르는 말이 없는 명령문도 low로 내려갑니다.** 승인·게시는 코드가 강제하므로 이 경우에도 무단 게시는 불가합니다.
  - `ignore-instructions`: "이전 지시를 무시", "ignore previous instructions"
  - `claim-no-issues`: "문제가 없다고 답", "report no issues"
  - `post-without-approval`: "승인 없이/바로 게시", "post this review now"
- **(다음 버전) 더미로 표시된 비밀값:** 탐지된 줄의 `#` 주석이나 바로 위 1–2줄 주석 줄에 더미·예시·dummy·example·test·fake·sample 같은 표시가 있으면 "참고: 더미로 표시된 비밀값"(low)으로 낮추고 꼭 고칠 것과 요약에서 뺍니다(지적과 마스킹은 그대로). 값 자체(따옴표 안)는 보지 않아 평가셋 18번(`…EXAMPLE` 키, 주석 없음)은 high입니다. **그래서 더미라고 적힌 진짜 키는 low로만 잡힙니다.** 도구는 진짜 키인지 확인하지 못합니다.
- RAGAS 값에는 판정 모델의 성향이 섞이고(14·17번), faithfulness의 ruff 출력은 2차 실행 후 같은 diff로 다시 계산해 채웠습니다. 케이스마다 1회 실행이라 변동성은 재지 않았습니다.

### 다음 단계
- 요약 규칙 보강 (지적 0건이면 "찾은 문제 없음"과 검사 범위만)
- **사내 LLM 이식:** `config.get_llm()`만 교체하고 같은 평가셋(본 세트 + 추가 세트)을 다시 실행해 통과율·일치율을 비교
- LangChain 1.x 문서 코퍼스 추가 (지금은 PEP 8 5개 섹션 + 매핑표)
- 실제 PR 코멘트 게시 (지금은 dry-run), GitHub MCP PR 조회(C1)
- GUI 디자인 다듬기 (Hallmark 적용 이후의 추가 개선), 리뷰 기록 보관
- 그 밖에: 케이스마다 반복 실행과 다수결, 줄 단위 정답 라벨과 탐지율·오탐률, 범용 LLM 리뷰(기준선)와의 비교, 별도 보안 리뷰어, Java 지원

## 핵심 코드 위치

| 파일 | 함수 | 역할 |
|---|---|---|
| [src/agent.py](src/agent.py) | `build_graph` | 메인 에이전트 그래프 (노드 함수 `input_guardrail` → `planner` → `review_team` → `output_validate` → `synthesize` → `render` → `hitl` → `post`) |
| [src/agent.py](src/agent.py) | `run_query`, `resume_review` | 핵심 함수 (`answer`·`contexts`·`trace` 반환), HITL 재개 |
| [src/tools.py](src/tools.py) | `build_tools` (`ruff_check`, `search_evidence`), `run_ruff`, `github_post_comment` | 도메인 도구 (게시는 dry-run) |
| [src/retriever.py](src/retriever.py) | `build_retriever`, `HybridRetriever.search`, `rrf_merge` | RAG 파이프라인 (BM25 + 벡터, RRF) |
| [src/reviewers.py](src/reviewers.py) | `plan_reviews`, `build_reviewer`, `run_review_team` | 규칙 기반 planner, `create_agent` 리뷰어, supervisor |
| [src/guardrails.py](src/guardrails.py) | `check_input`, `check_output`, `cross_check_pep8`, `masking_middleware` | 입력 가드레일, 근거 존재 검사, C2, 마스킹 미들웨어 |
| [src/schema.py](src/schema.py) | `Evidence` → `Finding` → `Review` | 출력 스키마 |
| [src/render.py](src/render.py) | `render_review` | 터미널 Markdown 렌더링 |
| [evaluation/run_eval.py](evaluation/run_eval.py) | `judge_case`, `alignment_counts`, `main` | 인-아웃 평가, 근거-지적 일치율 |
| [src/gui_server.py](src/gui_server.py) | `list_commits`, `start_job`, `decide` | 시연용 로컬 GUI (커밋 목록, 별도 스레드 리뷰, 승인/거절) |
| [evaluation/run_ragas.py](evaluation/run_ragas.py) | `main` | RAGAS 4개 지표 |
