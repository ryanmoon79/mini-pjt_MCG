# 3차 자체 평가 리포트 · CiteReview (다음 버전, D1~D6 이후)

> 평가 규칙과 지표 정의는 [evaluation/README.md](README.md)를 따릅니다. 평가 diff는 교육용 더미 데이터입니다. 2차(제출 기준, 태그 `submission-round2`) 결과는 [round2_report.md](round2_report.md)에 그대로 있습니다.
>
> **상태: 아직 실행하지 않았습니다.** 아래 표의 빈칸은 D5 실행 후 채웁니다. 실행할 조건(A·B 중 무엇을, 전체·핵심 중 무엇으로)은 사용자가 정합니다.

## 0. 조건 구분 (모델이 다르면 결과를 섞지 않음)

| 조건 | 코드 | 리뷰 모델 | 판정 모델 | 케이스 범위 | 실행 기록 폴더 | 목적 |
|---|---|---|---|---|---|---|
| 2차 (참고) | 태그 `submission-round2` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | `us.amazon.nova-pro-v1:0` | 22건 | `runs/round2/` | 제출 기준 |
| **A** | main (submission-round2 태그 이후) | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | `us.amazon.nova-pro-v1:0` | (전체 22건 / 핵심 7건 `--core`) | `runs/round3-haiku/` | 2차와 비교 (같은 모델, 코드만 다름) |
| **B** | main (submission-round2 태그 이후) | `openai.gpt-oss-120b-1:0` | `us.amazon.nova-pro-v1:0` | (전체 22건) | `runs/round3-gpt-oss/` | 모델 이식 확인 |

- 실행 명령과 옵션은 [README.md 5절](README.md#5-실행-기록과-실행-방법)에 있습니다. 실행 기록의 `run_meta.json`·`summary.json`에 리뷰 모델 ID가 남습니다.
- 케이스당 1회 실행이며 모델 출력은 실행마다 달라질 수 있습니다(README 2절 한계). A와 B의 차이는 모델과 실행 간 변동이 섞인 값입니다.

## 1. 조건 A — 현재 코드 + Haiku 4.5

(D5 실행 후 기록: 통과율(전체 또는 핵심 7건), 2차 대비 바뀐 케이스, 근거 확인 비율, 근거-지적 일치율, `summary_replaced`, `syntax`, `structured`, `usage`, 응답 시간)

## 2. 조건 B — 현재 코드 + gpt-oss-120b

(D5 실행 후 기록: 위와 같은 항목 + `structured`의 finalize·none 비율, `syntax.passed_because_dropped`)

## 3. 따로 확인할 케이스

4·14·15·17번(E501), 19번(인젝션), 10번(파싱 실패 주장 오탐) — 조건별로 나눠 적습니다.

## 4. 커밋 재리뷰와 토큰 (D4-4)

day07 `23402b2`, day03 `b24f206`(290줄, 기본값), day05 `13ee4ae` 재리뷰 비교와, day06 `b465f63`(338줄, `--max-lines 500`) 토큰. 500줄 기준 토큰은 실측이면 실측 diff를, 아니면 두 점 선형 추정치임을 적습니다. 어떤 리뷰 모델로 쟀는지 함께 적습니다.
