# 미니 PJT: CiteReview — 근거를 다는 코드 리뷰 에이전트

> ## 평가 대상: `main` 최신 커밋 (최종 버전)
>
> 과제 마감 시점 상태는 태그 [`submission-round2`](../../tree/submission-round2)로 보존했고, 그 뒤의 개선과 재평가는 [round3](evaluation/round3_report.md) · [round4](evaluation/round4_report.md) · [round5](evaluation/round5_report.md) 리포트에 있습니다. **이 README의 최종 평가 결과는 round5 기준입니다**(커밋 `ee461d5` 코드 + Sonnet 4.6).

## 무엇을 푸나

LLM 코드 리뷰는 근거가 없어 믿고 고칠지 판단하기 어렵고, LangChain 0.x → 1.x 전환기에는 이미 제거된 API를 놓치거나 오히려 추천하기 쉽습니다.

**CiteReview는 LangChain/LangGraph 1.x로 실습하는 수강생의 PR diff를 받아 요약 5줄·잠재 버그·스타일 이슈·추천 개선안을 내놓고, 모든 지적에 확인할 수 있는 근거를 붙입니다.**

- **근거 3종:** PEP 8 섹션·deprecated API 매핑표(문서), ruff·비밀값 탐지(도구), diff의 줄 위치(코드). 이번 리뷰에서 실제로 확인되지 않는 근거는 출력 전에 코드가 지웁니다.
- **구버전 API 탐지:** 설치된 1.x 기준 매핑표로 `LLMChain`·`create_react_agent` 같은 잔재를 찾고 1.x 대체 코드를 제안합니다. `langchain_classic` 권장은 코드가 결정적으로 막습니다.
- **가드레일:** 비밀값·개인정보는 LLM 호출 전에 마스킹하고, diff 안의 지시문(프롬프트 인젝션)은 따르지 않으며, 게시는 사람 승인 후에만 합니다(이번 범위는 dry-run).
- 자세한 기획은 [SERVICE.md](SERVICE.md)에 있습니다.

### 핵심 결과 한눈에

> **최종 평가 = round5**: 리뷰 모델 Claude Sonnet 4.6(`us.anthropic.claude-sonnet-4-6`, Bedrock `us-west-1`), 커밋 `ee461d5` 코드, 판정 Nova Pro. 2차 제출 결과(태그 `submission-round2`, Haiku 4.5)는 비교 열로 남깁니다. **리뷰 모델과 코드가 함께 바뀌어 직접 비교가 아닙니다.** 자세한 내용은 [round5_report.md](evaluation/round5_report.md).

| 항목 | **round5 (Sonnet 4.6, 최종)** | 2차 (Haiku 4.5, 제출 `submission-round2`) |
|---|---|---|
| 인-아웃 통과율 (본 세트 20건) | **20/20** | 20/20 (1차 9/20 → 재채점 15/20 → 2차 20/20) |
| 추가 세트 (함정 케이스 21·22) | **1/2** (실패 22: 정렬된 import diff에 가정형 bug 지적) | 2/2 |
| 근거 확인 비율 (존재) | **1.0** (29 → 29) | 1.0 (31 → 31) |
| 근거-지적 일치율 (타당) | **0.8** (4/5), C2 제거 1건 | 1.0 (8/8), C2 제거 0건 |
| 커밋 재리뷰 bug 정밀도 | round4: 1/5 = 0.20 (판단 필요 포함 0.40, 표본 5건). round5는 판단 안 함 | (측정 안 함) |
| 전체 실행 시간 (22건, 동시 2개) | **3분 20초** | 2분 55초 |
| 케이스당 토큰 (평균, 본 세트) | **입력 36,918 · 출력 1,148 · 호출 9회** | (기록 없음) |
| RAGAS | **faithfulness 0.749 · answer_relevancy 0.290 · context_precision 0.453 · context_recall 1.000** ([아래](#ragas-평가-결과)) | 0.729 · 0.264 · 0.711 · 1.000 |

- round4·5는 Sonnet의 오탐 줄이기(리뷰어 판단 규칙, 실패 경로 규칙, 출력 검증 ⑥ API 존재 주장·⑦ 자기부정·⑧ ruff 자동 수정·⑨ 이름 미정의 주장) 뒤의 결과입니다. round3 Sonnet(같은 모델, 이전 코드) 대비 본 세트 18 → 20, 평가셋 bug 지적 29 → 14(round4), 재리뷰 화면 bug·security 13 → 5건(round4)입니다.

### 리뷰 모델 — 과정 고정 스택과 달라진 점

과정 고정 스택(상위 `AGENTS.md`)은 리뷰 LLM으로 **Sonnet 4.5(`us-east-1`)**를 정하지만, 이 프로젝트의 최종 결과는 **Sonnet 4.6(`us-west-1`)**입니다. 모두 사용자 결정이며 이유는 아래와 같습니다(자세한 내용은 회고와 [round3_report.md](evaluation/round3_report.md)).

| 순서 | 리뷰 모델 | 쓴 평가 | 바꾼 이유 |
|---|---|---|---|
| 1 | Sonnet 4.5 (`us-east-1`, 과정 고정값) | — | 개발 중 Bedrock 계정 일일 토큰 한도(`Too many tokens per day`)로 막힘 |
| 2 | Haiku 4.5 (`us-east-1`) | 1·2차(제출), 3차 A | 같은 일일 한도로 3차 측정이 중간에 끊김 |
| 3 | gpt-oss-120b (`us-east-1`) | 3차 B | 결과 도구를 건너뛰거나 JSON 글로 답해 결과 수집이 불안정(structured none, 종합 노드 글 응답) |
| 4 | **Sonnet 4.6 (`us-west-1`)** | 3차 C, round4, **round5(최종)** | 결과 수집이 가장 안정적(none 0)이고 bug를 가장 많이 찾음. 단점(오탐·토큰)은 round4·5에서 줄임 |

그 밖에 과정 고정 스택과 다른 점: 체크포인터는 `SqliteSaver`(파일) 대신 같은 프로세스 재개용 `InMemorySaver`, 평가 판정·RAGAS 모델은 리뷰 모델과 다른 Nova Pro(평가 전용). 임베딩(Titan v2)·Chroma 설정은 과정 고정값 그대로입니다.

## 활용한 패턴 (Day 1–7)

과정 공식 패턴 12개 중 **10개 적용 + 1개 일부**(10번: 규칙 기반 planner만, 장기 메모리 없음), **MCP 미구현**(선택 작업 C1).

| # | 패턴 | Day | CiteReview에서 쓰는 곳 |
|---|---|---|---|
| 1 | LCEL chain (Pydantic 구조화 출력) | Day 1 | 종합 노드: `SYNTH_PROMPT \| llm.with_structured_output(SummaryDraft)`로 요약 5줄·추천 개선안 생성 (형식 오류 시 1회 재생성) |
| 2 | ReAct (도구 자율 선택) | Day 3 | 리뷰어 2종이 `create_agent`로 `ruff_check`·`search_evidence` 중 필요한 도구를 스스로 고름 |
| 3 | RAG (하이브리드 검색) | Day 2 | `src/evidence/` (재노출 `src/retriever.py`): PEP 8·deprecated 매핑표 12청크(`rule_id` 메타데이터), **BM25 + Chroma 벡터를 RRF로 직접 합침** (`EnsembleRetriever`는 1.x에서 `langchain_classic`으로 옮겨져 쓰지 않음). **리랭킹·쿼리 확장은 미적용** |
| 4 | 도구 다중 | Day 4 | 한 리뷰에서 `ruff_check` → `search_evidence`(ruff 결과의 `pep8_rule_id`로 PEP 8 섹션 검색)를 이어서 사용 |
| 5 | MCP 서버 연동 | Day 4 | **미구현** — GitHub MCP PR 조회(`github_get_pr`)는 선택 작업 C1 |
| 6 | 가드레일 | Day 5 | 입력: 비밀값·PII 마스킹, 인젝션 탐지, 범위(`.py`만, 기본 300줄·최대 500) / 출력: 스키마, ① 근거 실재 확인, ② PEP 8 근거 ruff 교차 확인(C2), ③ `langchain_classic` 권장 제거, ④ 파싱 실패 주장 검증(`ast.parse`), ⑤ 줄 길이 지적을 ruff로 생성, ⑥ API 존재 주장 검증(실제 import), ⑦ 자기부정 지적 제거, ⑧ ruff 자동 수정 결과로 고치는 방법, ⑨ 이름 미정의 주장 정적 분석 |
| 7 | HITL | Day 5 | 게시 노드 앞 `interrupt()`로 승인·거절·항목별 제외 (`InMemorySaver`로 같은 프로세스 안에서 재개, 게시는 dry-run + 허용 목록) |
| 8 | 미들웨어 | Day 5 | 모든 리뷰어에 모델 호출 직전 마스킹 미들웨어(`wrap_model_call`) + `ModelCallLimitMiddleware` |
| 9 | Multi-Agent Supervisor | Day 6 | `langgraph_supervisor.create_supervisor` + 버그·스타일 리뷰어 (supervisor가 계획된 리뷰어를 건너뛰면 코드가 직접 실행) |
| 10 | Plan-Execute · 장기 메모리 | Day 7 | **일부** — 규칙 기반 planner만(버그 리뷰어는 항상, 스타일 리뷰어는 추가된 줄이 있을 때). 장기 메모리(LangGraph Store)는 없음 |
| 11 | Observability · Trace | Day 7 | `run_query`의 `trace`(시각 포함 결정적 이벤트: 노드·도구 호출·가드레일 전후 지적 수·HITL), 평가 기록 `trace.json`, `.env`에 LangSmith 키가 있으면 LangSmith |
| 12 | 평가 (RAGAS · LLM-as-Judge) | Day 7 | 인-아웃 세트 22건으로 1–5차 자체 평가(최종 round5), RAGAS 4개 지표(round5), Nova Pro로 문장형 항목 판정(코드로 판정할 수 있는 항목은 코드로), 근거-지적 일치율·커밋 재리뷰 정밀도 |

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

### 평가자용 빠른 확인

비용·권한이 적게 드는 순서입니다. 숫자는 round5 실행 기록 기준이고 모델 응답은 실행마다 조금씩 다릅니다.

| 단계 | 명령 | 예상 결과 |
|---|---|---|
| ① AWS 없이 | `python -m pytest tests -q` | 98건 통과 (모두 가짜 모델 — 평가 diff 03·10·19번 등을 가짜 모델로 그래프 끝까지 돌리는 테스트 포함) |
| | `python evaluation/validate_test_queries.py` | 마지막 줄 `RESULT: ALL PASS` (CSV 22건 형식·건수) |
| ② AWS 최소 | `python -m src.interfaces.cli review data/eval_diffs/03_llmchain.diff --batch` | 약 20–35초(첫 실행은 코퍼스 12청크 임베딩 색인 포함), 입력 약 3.8–3.9만·출력 약 2.1–2.2천 토큰(Sonnet 4.6), 승인 대기에서 멈춤 |
| ③ 전체 평가 | `python evaluation/run_eval.py --review-model sonnet` | 약 3–4분(동시 2개) + 판정. 입력 약 79만·출력 약 2.5만 토큰(리뷰 모델) + Nova Pro 판정 호출 |

- **일일 토큰 한도 주의:** ③과 커밋 재리뷰를 같은 날 모두 돌리면 계정 일일 한도에 걸릴 수 있습니다(아래 "운영 안내").
- **필요한 Bedrock 권한** (`bedrock:InvokeModel`, Converse API, 콘솔에서 모델 접근 허용):

| 용도 | 모델 ID | 리전 | 필요한 단계 |
|---|---|---|---|
| 리뷰 (기본) | `us.anthropic.claude-sonnet-4-6` (추론 프로필) | `us-west-1` | ②③ |
| 임베딩 | `amazon.titan-embed-text-v2:0` | `us-east-1` | ②③ (첫 색인), RAGAS |
| 평가 판정·RAGAS | `us.amazon.nova-pro-v1:0` | `us-east-1` | ③, RAGAS |
| (선택) 비교 모델 | `us.anthropic.claude-haiku-4-5-20251001-v1:0`, `openai.gpt-oss-120b-1:0` | `us-east-1` | `--review-model haiku`·`gpt-oss` |

- **Sonnet 4.6이나 `us-west-1`을 쓸 수 없는 계정:** `--review-model haiku`(2차 제출 때의 모델, `us-east-1`)로 돌릴 수 있습니다 — CLI `review ... --review-model haiku`, 평가 `run_eval.py --review-model haiku`. 과정 표준 Sonnet 4.5로 돌리려면 [src/shared/config.py](src/shared/config.py)의 `REVIEW_MODELS`에 `"sonnet45": "us.anthropic.claude-sonnet-4-5-20250929-v1:0"` 한 줄을 넣고 `--review-model sonnet45`로 실행합니다(리전은 자동으로 `us-east-1`). **이때 결과는 리포트 숫자(Sonnet 4.6 기준)와 다를 수 있습니다** — 2차 결과가 Haiku 기준입니다.

**1. 환경** (Python 3.12 이상. 프로젝트 안에 가상환경을 만들면 `run.bat`도 그 환경을 씁니다)

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS·Linux: source .venv/bin/activate
pip install -r requirements.txt
```

프로젝트 루트에 `.env`를 만들고 Bedrock 접근 키를 넣습니다 (커밋하지 않음). 리전은 코드가 모델별로 정하므로(`src/shared/config.py`) `.env`에 넣지 않아도 됩니다.

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
CITEREVIEW_ALLOWED_POST_REPOS=owner/repo   # (선택) 게시 허용 저장소, 쉼표 구분
```

**2. 리뷰** (처음 실행할 때 코퍼스 12청크를 `./chroma_db`에 색인합니다)

```bash
git diff -U9999 > my.diff                                            # 리뷰할 변경을 diff로 저장
python -m src.interfaces.cli review my.diff                                     # 리뷰 출력 → 게시 승인 여부 질문
python -m src.interfaces.cli review data/eval_diffs/03_llmchain.diff --verbose  # 내부 로그 포함
python -m src.interfaces.cli review my.diff --batch                             # 승인 대기에서 멈추고 결과 JSON 출력
python -m src.interfaces.cli review my.diff --repo-dir ../my-repo --max-lines 500  # 저장소 ruff 설정 + 변경 한도 500
```

변경 한도는 기본 300줄이고 `--max-lines`(최대 500) > 저장소 `pyproject.toml [tool.citereview] max-lines` > 300 순서로 정합니다. `--repo-dir`를 주면 그 저장소의 ruff 설정(`ruff.toml`·`.ruff.toml`·`pyproject.toml [tool.ruff]`)으로 검사합니다. 적용된 기준은 결과 맨 위 줄과 요약 4줄에 보입니다.

승인 질문에는 `y`(승인), `n`(거절), `i 2 3`(2·3번 항목을 빼고 승인)으로 답합니다. 게시는 dry-run이라 코멘트 내용만 출력합니다.

**(실험 옵션) 리뷰어 diff 문맥 줄이기:** [src/shared/review_settings.py](src/shared/review_settings.py)의 `REVIEWER_DIFF_CONTEXT`를 `"focused"`로 바꾸면 리뷰어에게 바뀐 줄이 든 함수·클래스 + import + 참조하는 모듈 상수만 넘깁니다(출력 검증은 파일 전체). 9건 실험에서 큰 커밋의 입력 토큰이 67–71% 줄었고 재현율 손실은 보이지 않았습니다([round5_report.md 7절](evaluation/round5_report.md)). 기본값은 `"full"`(받은 diff 그대로)이며, 최종 평가(round5)도 `full` 기준입니다. 다음 단계에서 기본값 전환 예정입니다.

**3. 테스트** (가짜 모델만 써서 실제 API를 호출하지 않음, 98건)

```bash
python -m pytest tests -q
```

**4. 평가** — 결과는 `evaluation/runs/<실행ID>/`에 저장 (커밋 제외)

```bash
python evaluation/validate_test_queries.py           # CSV 형식·건수 검증 (모델 호출 없음)
python evaluation/run_eval.py --review-model sonnet                 # 본 세트 20건 + 추가 세트 2건 (동시 실행 2개)
python evaluation/run_eval.py --review-model sonnet --cases 3,4,10  # 일부 케이스
python evaluation/run_eval.py --rejudge <실행ID>     # 저장된 기록을 현재 판정기로 다시 채점 (리뷰 모델 호출 없음)
```

**5. RAGAS** — ragas 의존성(`scikit-network`)이 Python 3.14 휠을 제공하지 않아 Python 3.12 평가 전용 환경을 따로 씁니다. `run_ragas.py`는 `src/`를 import하지 않고 실행 기록만 읽습니다.

```bash
uv venv .venv-eval --python 3.12
uv pip install --python .venv-eval/Scripts/python.exe -r requirements-eval.txt
.venv-eval/Scripts/python.exe evaluation/run_ragas.py <실행ID>
```

**운영 안내 — Sonnet 일일 토큰 한도.** 리뷰 모델 Sonnet 4.6은 Bedrock 계정의 일일 토큰 한도(`Too many tokens per day`)가 있습니다. round5 재테스트에서 평가 22건(케이스당 입력 약 3.7만 토큰)을 돌린 날 커밋 재리뷰 5건(건당 입력 약 3.7만–73.5만 토큰, 큰 커밋일수록 많음)까지 이어서 돌리자 마지막 2건이 한도에 걸렸고, 다음 날 다시 돌려 끝냈습니다. **평가 22건과 커밋 재리뷰는 날을 나눠 실행하세요.** 한도에 걸린 실행은 답변 첫 줄이 "리뷰 중 오류가 났습니다: ThrottlingException"이고 trace `error`에 "Too many tokens per day"가 남으므로, 그 건만 다시 돌리면 됩니다. RAGAS 판정(Nova Pro, `us-east-1`)은 리뷰 모델과 다른 모델입니다.

**6. 시연용 로컬 GUI** — 표준 라이브러리 `http.server`로 `127.0.0.1:8765`에서만 열립니다. CLI와 같은 `run_query`·`resume_review`를 씁니다.

```bash
python -m src.interfaces.gui_server  # 브라우저가 열림
# Windows: run.bat 더블클릭 / 맥·리눅스(Git Bash 포함): bash run.sh — 둘 다 프로젝트 .venv의 Python으로 실행
```

**폴더 선택** 버튼(맨 앞에 뜨는 Windows 폴더 창)이나 경로 입력으로 저장소를 고르면 커밋 카드 목록(메시지, 해시, 날짜, `.py` 변경 줄 수, 적용 한도 초과 표시)이 나오고 카드를 눌러 리뷰합니다. 변경 한도 입력칸(비우면 저장소 설정 또는 300, 최대 500)을 바꾸면 초과 표시가 새 한도로 다시 계산되고, 커밋 리뷰는 그 저장소의 ruff 설정을 따릅니다. 또는 diff 파일을 고르면, 진행 단계(입력 가드레일 → planner → 리뷰어 → 출력 검증 → 종합 → 렌더링 → 승인 대기)와 CLI와 같은 고정폭 결과가 나오고, 승인/거절 버튼으로 dry-run 게시를 결정합니다.

**7. 로컬 API** — `python -m src.interfaces.api_server`(127.0.0.1:8766)가 `POST /query` `{"question": "data/eval_diffs/03_llmchain.diff 를 리뷰해줘"}`에 `run_query` 결과(`answer`·`contexts`·`trace`)를 그대로 돌려줍니다. 승인 대기에서 멈추고 게시하지 않으며, 표준 라이브러리만 쓰고 Host 헤더가 이 서버 자신일 때만 받습니다. **Docker 이미지는 제공하지 않습니다**(Dockerfile 없음).

## RAGAS 평가 결과

**round5(최종)** 실행 기록(본 세트)으로 계산했습니다. 판정 LLM은 리뷰 모델과 다른 Nova Pro, 임베딩은 Titan v2입니다. 2차는 비교용입니다.

| 지표 | round5 (Sonnet 4.6) | 대상 건수 | 2차 (Haiku 4.5) |
|---|---|---|---|
| context_recall | **1.000** | 9건 중 8건 채점 | 1.000 (9/9) |
| context_precision | **0.453** | 9 | 0.711 (9/9) |
| faithfulness | **0.749** | 19건 중 18건 채점 | 0.729 (19/19) |
| answer_relevancy | **0.290** | 14 | 0.264 (10/10) |

- **faithfulness 설정:** contexts에 검색 청크뿐 아니라 마스킹된 diff hunk와 리뷰 중 ruff 출력을 넣었습니다. 리뷰는 diff와 도구 출력을 근거로 쓰는데, 검색 청크만 넣으면 diff에 대한 문장이 모두 "근거 없음"이 되기 때문입니다(2차, 검색 청크만: 0.466).
- **context_precision 하락(0.711 → 0.453):** 근거 목록 순서는 바꾸지 않고, 정답 청크의 검색 순위를 함께 보고합니다. round3 이후 줄 길이 근거 청크가 목록 끝에 붙고(4·15·17번), Sonnet 버그 리뷰어의 넓은 deprecated 검색이 정답 앞에 청크를 쌓습니다(4·6·15·17번). context_recall과 판정 통과에는 영향이 없습니다. 케이스별 순위표는 [round5_report.md 4절](evaluation/round5_report.md).
- **미채점 2건(faithfulness 6번, context_recall 17번):** 판정 모델이 형식이 틀린 JSON을 내 파싱되지 않았고, 그 케이스만 다시 돌려도 같았습니다. 평균은 채점된 케이스로 냈습니다.
- **answer_relevancy**는 질의가 "…diff 를 리뷰해줘" 같은 짧은 명령이라 구조적으로 낮게 나옵니다. 2차 해석은 [round2_report.md 7절](evaluation/round2_report.md#7-ragas-4개-지표-본-세트).

## 인-아웃 세트 통과율 (자체 평가)

평가셋: [evaluation/test_queries.csv](evaluation/test_queries.csv) 본 세트 20건(positive 8 / negative 4 / edge 5 / guardrail 3) + 추가 세트 2건. 리뷰 모델 1·2차 Claude Haiku 4.5, 3차 B gpt-oss-120b, 3차 C·round4·round5 Sonnet 4.6, 케이스마다 1회. 규칙은 [evaluation/README.md](evaluation/README.md)를 따릅니다.

- **1차:** 9 / 20 통과 → 같은 기록을 판정기 v2와 정정된 CSV로 재채점하면 15 / 20 ([round1_report.md](evaluation/round1_report.md))
- **2차 (개선 후):** **20 / 20** 통과, 추가 세트 2 / 2, 실행 오류 0건 ([round2_report.md](evaluation/round2_report.md))
- **개선폭: +11건**
  - 9 → 15 (+6): 판정기 오판 5건 해소, CSV 13번 정정(AttributeError → TypeError). 에이전트 출력은 그대로
  - 15 → 20 (+5): 호출 한도로 끝나지 못한 4건(재시도·호출 한도와 형식 오류 분리·동시 실행 2개), `langchain_classic` 권장 문장 1건(결정적 검사)
- 그 밖에: 근거 확인 비율 1.0, 근거-지적 일치율 1.0(8/8), guardrail 3/3, 원문 노출·무단 게시 0건, `tests/` 34건 통과
- **3차 B (다음 버전 코드 `00807f1` + gpt-oss-120b, 2차와 직접 비교 아님):** **17 / 20**, 추가 세트 1 / 2, 실행 오류 0건 ([round3_report.md](evaluation/round3_report.md))
  - 실패 원인: 4번은 **코드**(줄 길이 지적을 코드가 만들면서 리뷰어가 `search_evidence`를 부르지 않음 — CSV `expected_tools`와 불일치), 9·22번은 **모델**의 과잉 지적, 10번은 종합 노드가 구조화 출력 대신 글로 답한 **모델** 응답 형식(코드에 대비 없음)
  - 리뷰어 결과를 받지 못한 경우(`structured` none): 리뷰어 실행 51회 중 2회, 원인은 결과를 JSON 글로 답하는 gpt-oss 습관(크기와 무관해 보임)
  - 3차 A(같은 코드 + Haiku 4.5)는 14/22건에서 Sonnet 확정으로 미완료 종료
- **3차 C (같은 `00807f1` 코드 + Sonnet 4.6):** 18 / 20(수정 기준), 추가 세트 1 / 2 ([round3_report.md](evaluation/round3_report.md))
- **round4 (커밋 `542b0bb` + Sonnet 4.6):** **20 / 20**, 추가 세트 1 / 2(22번), 실행 오류 0건, structured none 0 ([round4_report.md](evaluation/round4_report.md))
  - C에서 실패한 9번(`create_agent`가 없다는 오탐)·17번(E711을 bug로도 지적)이 통과. 근거-지적 일치율 0.364 → 0.667
  - 커밋 재리뷰 4건의 bug 정밀도 0.15 → 0.20(표본 5건이라 경향으로만). 남은 오탐은 "이런 값으로 호출되면" 같은 가정형 경로
- **round5 (커밋 `ee461d5` + Sonnet 4.6, 최종 평가):** **20 / 20**, 추가 세트 1 / 2(22번), 실행 오류 0건, structured none 0, 근거-지적 일치율 0.8(4/5), 전체 재테스트 ①–⑥ 통과 ([round5_report.md](evaluation/round5_report.md))
  - round4 이후 개선(⑧ ruff 자동 수정, ⑨ 이름 미정의 주장 검증, ruff 중립 폴더, 참고 지적 섹션, "확인 필요" 집계, GUI 선택 표시) 반영. 커밋 재리뷰에서 ⑨가 day03 260행 오탐을 제거, f8e0c93 가짜 I001 해소

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
- **정규식 동작에 대한 보안 지적은 직접 확인이 필요합니다.** cd65c0f 84행의 "정규식 우선순위 때문에 정상 문장도 차단된다"는 지적은 그 커밋 코드로 실행해 보면 사실이 아니었지만, 같은 diff에서 GUI 리뷰 6번 중 3번 반복됐고 요약의 "먼저 할 일"에도 올라갔습니다. 지금 출력 검증은 정규식 주장을 실행해 확인하지 않습니다(다음 단계).
- **(round4) 가정형 경로 오탐:** Sonnet은 이 diff의 코드에 없는 입력("빈 목록으로 호출되면", "필드가 문자열이면")을 실패 경로로 적은 bug를 냅니다. 프롬프트 규칙으로 줄었지만 남았고, 결정적으로 거르려던 "high bug는 다른 줄 근거 필수" 규칙은 제거된 API·같은 줄 None 버그 같은 맞는 지적까지 낮춰 철회했습니다.
- **(round4) 재현율:** round3에서 타당하던 day07 94행 지적(None id)이 round4 이후 10회 실행에서 한 번도 나오지 않았습니다. 실패 경로 규칙을 끈 실행에서도 나오지 않아 원인은 확정하지 못했고, 평가셋에는 이런 케이스가 없어 재현율을 따로 재지 못합니다.
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

### round5 이후, 제출 전 결정적 수정

모델 호출 없이 코드로 판정하는 작은 수정만 넣었습니다. 저장된 리뷰 123건에 다시 적용해 맞는 지적이 빠지지 않음을 확인했고(오탐 1건만 제거), **평가 숫자는 round5 그대로입니다**(실제 모델 재평가 안 함). 자세한 내용과 실제 리뷰 사례(cd65c0f, ac91538, f8e0c93 처음 vs 최종)는 [round5_report.md 7절](evaluation/round5_report.md).

1. F401 같은 ruff 스타일 위반이 bug와 style로 두 번 나오던 중복 제거 (출력 검증. 함께 시도한 프롬프트 한 줄은 타당한 지적을 빠뜨려 되돌림)
2. ⑦ 자기부정 표현 추가 ("실행 실패 경로가 없습니다", "이 지적은 제거해야 합니다")
3. ⑨ 이름 미정의 주장에 "초기화되지 않은 상태" 계열 추가
4. 게시 차단 문구의 조사 은/는
5. GUI 서버를 이미 쓰는 포트에서 다시 실행하면 안내 후 종료 (같은 포트에 두 서버가 떠서 수정 전 코드가 요청을 받던 문제)
6. 요약 5줄이 파일과 줄을 함께 말하면 그 (파일, 줄)이 지적 목록에 있어야 함 (저장된 요약 128개에서 바뀌는 것 0개)
7. `run_ragas.py --cases` 부분 재실행이 요약 칸·평균을 덮어쓰지 않고 기존 결과에 병합 (round5 기록으로 확인: 0.7489, 18/19 그대로)

### 다음 단계

**최종 평가 결과는 round5 기준입니다.** 아래는 round5 이후 계획으로, 측정 방법과 근거는 [round5_report.md 7절](evaluation/round5_report.md)에 있습니다.

- **(맨 앞) 고치는 방법 문법 검증:** 수정안을 변경 후 파일에 적용해 본 뒤 `ast.parse`로 파싱되지 않으면 -/+ 대신 "자동으로 맞추지 못함, 직접 확인"으로 표시(cd65c0f 84행처럼 적용하면 코드가 깨지는 수정안 방지). 저장된 기록으로 먼저 측정
- **정규식 주장 실행 검증:** 지적이 "이 패턴이 X를 매칭한다/안 한다"고 예시 문자열을 들어 주장하면 패턴을 실제로 돌려 확인하고, 주장이 틀리면 내보내지 않음(cd65c0f 84행). **안전 원칙(⑥·⑨와 같음): 리뷰 대상 파일은 실행하지 않는다.** 패턴이 문자열 리터럴로 적혀 있으면 ast로 그 리터럴만 꺼내 컴파일해 확인하고, 모듈 상수를 조합하는 등 실행이 필요한 패턴은 "판단 필요"로 남긴다
- **확인되지 않은 보안 주장은 요약의 "먼저 할 일"에 올리지 않기:** 코드로 확인되지 않은 보안 지적이 요약 5줄의 첫 할 일이 되지 않게(cd65c0f 7번째 실행에서 84행 오탐이 "먼저 수정"으로 올라감)
- **토큰 줄이기:** 1-c 리뷰어 diff 문맥 `focused`를 기본값으로 전환(9건 실험으로 검증, 큰 커밋 입력 토큰 −67–71%), 1-a 버그 리뷰어 검색 규칙(diff에 나온 이름으로만 검색, 구버전 API가 없는 diff에서 deprecated 일반 검색 금지), 1-b 리뷰어 호출 상한 축소, 큰 커밋의 파일별 분할 리뷰
- **출력 검증 ⑨ 보완:** "if 블록 안에 있다" 같은 들여쓰기 주장의 ast 검증 (곁가지 문장 반응은 우선순위 낮음)
- **평가:** 입력 계약 위반 버그(day07 94행 유형) 재현율 케이스를 추가 세트로, round6 재평가
- **오탐:** 양보형 오탐("동작하지만 … 할 수 있다") 대응, 다른 지적을 되풀이하는 지적 걸러내기 — 저장된 기록으로 먼저 측정
- **Java 지원:** 최소 버전(약 1–2일) — `.java`를 받아 LLM 리뷰만 하고 근거는 코드 위치·Java 스타일 문서로, 근거 존재 확인(①)만 적용하고 Python 전용 검사는 건너뜀. Python 수준(약 1–2주) — Java 린터(Checkstyle·PMD 등, JRE 필요) 연동, Java 파서로 결정적 검증 재구현, Java 코퍼스·평가셋, 오탐 측정. 이미 있는 확장 지점은 파일 언어 판정 한 곳(`FileDiff.is_python`·`split_python`)과 코퍼스 청크의 `language` 메타데이터이고, **언어별 린터 레지스트리는 아직 없음**(ruff를 직접 호출). 주된 작업은 Python 전용 결정적 검증(④ 파싱 실패, ⑤ 줄 길이, ⑥ API 존재, ⑧ ruff 자동 수정, ⑨ 이름 미정의, C2, 1-c `focused`)을 Java용으로 다시 만드는 것. 평가셋 12번(Java만 있는 diff는 거절)의 기대 동작도 바뀜
- 요약 규칙 보강 (지적 0건이면 "찾은 문제 없음"과 검사 범위만)
- **사내 LLM 이식:** `config.get_llm()`만 교체하고 같은 평가셋(본 세트 + 추가 세트)을 다시 실행해 통과율·일치율을 비교
- LangChain 1.x 문서 코퍼스 추가 (지금은 PEP 8 5개 섹션 + 매핑표)
- 실제 PR 코멘트 게시 (지금은 dry-run), GitHub MCP PR 조회(C1)
- GUI 디자인 다듬기 (Hallmark 적용 이후의 추가 개선), 리뷰 기록 보관
- 그 밖에: 케이스마다 반복 실행과 다수결, 줄 단위 정답 라벨과 탐지율·오탐률, 범용 LLM 리뷰(기준선)와의 비교, 별도 보안 리뷰어

## 핵심 코드 위치

| 파일 | 함수 | 역할 |
|---|---|---|
| [src/review/graph.py](src/review/graph.py) | `build_graph` | 메인 에이전트 그래프 (노드 함수 `input_guardrail` → `planner` → `review_team` → `output_validate` → `synthesize` → `render` → `hitl` → `post`) |
| [src/review/graph.py](src/review/graph.py) | `run_query`, `resume_review` | 핵심 함수 (`answer`·`contexts`·`trace` 반환), HITL 재개. 과정 규약 경로 [src/agent.py](src/agent.py)로 재노출 |
| [src/review/team.py](src/review/team.py) | `plan_reviews`, `build_reviewer`, `run_review_team` | 규칙 기반 planner, `create_agent` 리뷰어, supervisor, 보완 단계 |
| [src/review/summary.py](src/review/summary.py) | `secbug_line`, `style_line`, `scope_line`, `change_violation` | 요약 2·3·4줄(코드)과 1·5줄(LLM) 규칙 검사 |
| [src/review/prompts.py](src/review/prompts.py) | `BUG_PROMPT`, `STYLE_PROMPT`, `SYNTH_PROMPT` | 리뷰 흐름의 LLM 프롬프트 |
| [src/tools.py](src/tools.py) → [src/toolkit/](src/toolkit/) | `build_tools` (`ruff_check`, `search_evidence`), `run_ruff`, `github_post_comment` | 도메인 도구 (게시는 dry-run). `src.tools`로 재노출 |
| [src/evidence/search.py](src/evidence/search.py) | `build_retriever`, `HybridRetriever.search`, `rrf_merge` | RAG 파이프라인 (BM25 + 벡터, RRF). 과정 규약 경로 [src/retriever.py](src/retriever.py)로 재노출 |
| [src/guardrails/](src/guardrails/) | `check_input`, `check_output`, `cross_check_pep8`, `verify_api_claims`, `masking_middleware` | 입력 가드레일, 근거 존재 검사, C2, API 존재 주장 검증, 마스킹 미들웨어 |
| [src/findings/schema.py](src/findings/schema.py) | `Evidence` → `Finding` → `Review` | 지적 데이터 모델 (여러 도메인 공통) |
| [src/report/render.py](src/report/render.py) | `render_review` | 터미널 Markdown 렌더링 |
| [src/shared/config.py](src/shared/config.py) | `get_llm`, `get_embeddings` | 모델·리전·환경 설정 (Sonnet 4.6, us-west-1) |
| [evaluation/run_eval.py](evaluation/run_eval.py) | `judge_case`, `alignment_counts`, `main` | 인-아웃 평가, 근거-지적 일치율 |
| [src/interfaces/gui_server.py](src/interfaces/gui_server.py) | `list_commits`, `start_job`, `decide` | 시연용 로컬 GUI (커밋 목록, 별도 스레드 리뷰, 승인/거절) |
| [src/interfaces/api_server.py](src/interfaces/api_server.py) | `Handler.do_POST`, `main` | 로컬 API `POST /query` (run_query 결과 그대로, 승인 대기에서 멈춤) |
| [evaluation/run_ragas.py](evaluation/run_ragas.py) | `main` | RAGAS 4개 지표 |

**과정 규약 경로:** 2차 제출 뒤 `src/`를 도메인 폴더로 나눴습니다(제출본은 태그 `submission-round2`). 공식 제출 구조의 세 파일은 그대로 있습니다 — `src/agent.py`(`run_query`·`resume_review`·`build_graph`), `src/tools.py`(`build_tools`·`run_ruff`·`github_post_comment` 등), `src/retriever.py`(`build_retriever` 등)는 도메인 폴더(`review/`·`toolkit/`·`evidence/`)의 코드를 다시 내보내는 파일이라 `from src.tools import ...` 같은 규약 경로가 그대로 동작합니다. 도구 패키지는 `src/tools.py` 파일과 이름이 겹치지 않도록 `src/toolkit/`으로 두었고, `run_query`가 돌려주는 `answer`·`contexts`·`trace` 형식도 같습니다.
