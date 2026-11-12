# 출력 예시 (기준 화면)

> 터미널 기본 출력의 기준입니다. 렌더러 구현과 리뷰는 이 화면에 맞춥니다. 내용은 교육용 더미 데이터입니다.
> 출력 구성은 [SERVICE.md](../SERVICE.md) 1번, 렌더링 구현 규칙은 [CLAUDE.md](../CLAUDE.md) 6번 "출력 렌더링"을 따릅니다. 내부 로그는 `--verbose`에서만 나옵니다.

```
CiteReview · data/eval_diffs/03_llmchain.diff
리뷰: app/chain.py, config.py · 건너뜀: README.md (.py 아님) · 마스킹: 비밀값 1건 · 기준: 줄 길이 88칸(CiteReview ruff.toml), 변경 한도 300줄(기본값)
꼭 고쳐야 할 것 2건 (보안 1, 버그 1)

## 요약
1. app/chain.py에 LLMChain 기반 리뷰 체인을 추가하고 config.py에 AWS 키 상수를 추가합니다.
2. 보안 지적 1건, 버그 지적 1건이며 꼭 고칠 것은 2건입니다 (먼저 볼 곳: app/chain.py 12행).
3. 스타일: 줄 길이 초과 1줄, 그 밖의 스타일 1건 (F401).
4. 리뷰 범위: app/chain.py, config.py; 건너뜀: README.md; 기준: 줄 길이 88칸(CiteReview ruff.toml), 변경 한도 300줄(기본값).
5. 먼저 config.py 4행의 AWS 키를 환경변수로 옮기고 app/chain.py 12행을 LCEL로 바꾸세요.

## 잠재 버그

### 보안

1. config.py:4 · high
   AWS 액세스 키가 코드에 그대로 있습니다. 저장소에 올라가면 누구나 이 키로 계정을 쓸 수 있습니다.
       3 | import os
     > 4 | AWS_KEY = "[MASKED:aws-access-key]"
       5 | REGION = "us-east-1"
   근거: 비밀값 탐지 (secret-scanner:aws-access-key)
   고치는 방법:
     - AWS_KEY = "[MASKED:aws-access-key]"
     + AWS_KEY = os.environ["AWS_ACCESS_KEY_ID"]

### 버그

2. app/chain.py:12 · high
   langchain.chains는 LangChain 1.x에서 제거되어 이 줄에서 ImportError가 납니다.
      11 | from langchain_core.prompts import ChatPromptTemplate
    > 12 | from langchain.chains import LLMChain
      13 |
   근거: deprecated API 매핑표 (DEP-LLMChain)
   고치는 방법:
     - from langchain.chains import LLMChain
     + from langchain_core.output_parsers import StrOutputParser
   체인은 chain = prompt | model | StrOutputParser() 로 만듭니다.

## 스타일 이슈

3. app/chain.py:3 · low
   os를 import하지만 쓰지 않습니다. 읽는 사람이 쓰이는 곳을 찾게 됩니다.
       2 |
     > 3 | import os
       4 | from langchain_aws import ChatBedrockConverse
   근거: ruff F401 (ruff:F401)
   고치는 방법:
     - import os

## 추천 개선안
- 다른 파일에도 langchain.chains, langchain.memory import가 남아 있는지 함께 확인하세요.
- 비밀값은 .env에서 읽고 .env는 .gitignore에 넣으세요.

## 참고
줄 길이 초과 1줄 (기준 88칸): app/chain.py 27 / ruff format 또는 괄호 안 줄바꿈으로 정리
(ruff:E501, PEP8-maximum-line-length, 길이는 --verbose)

지적 3건 (꼭 고칠 것 2 · 다듬을 것 1) · 근거가 확인되지 않아 제외된 지적 1건 (--verbose로 확인)
```

## 문제없는 diff (교육용 더미 데이터입니다)

지적이 하나도 없으면 맨 위 안내 줄 바로 다음에 "문제를 찾지 못했습니다."를 한 줄 보여 주고, 빈 소제목은 "(없음)"으로 둡니다.

```
CiteReview · data/eval_diffs/10_clean_refactor.diff
리뷰: app/utils.py · 건너뜀: 없음 · 마스킹: 없음 · 기준: 줄 길이 88칸(CiteReview ruff.toml), 변경 한도 300줄(기본값)
꼭 고쳐야 할 것 없음
문제를 찾지 못했습니다.

## 요약
1. app/utils.py에서 공백 정리 로직을 _clean 함수로 추출하고 타입 힌트를 추가합니다.
2. 보안·버그 지적은 없습니다.
3. 스타일 지적은 없습니다.
4. 리뷰 범위: app/utils.py; 건너뜀: 없음; 기준: 줄 길이 88칸(CiteReview ruff.toml), 변경 한도 300줄(기본값).
5. 꼭 고쳐야 할 것은 없습니다.

## 잠재 버그

### 보안

(없음)

### 버그

(없음)

## 스타일 이슈

(없음)

## 추천 개선안
- normalize의 동작을 확인하는 단위 테스트를 함께 추가하면 좋습니다.

지적 0건 (꼭 고칠 것 0 · 다듬을 것 0) · 근거가 확인되지 않아 제외된 지적 0건 (--verbose로 확인)
```

고치는 방법의 `+` 줄이 5줄을 넘으면 앞 5줄만 보여 주고 `…(전체는 --verbose)`로 줄입니다. 전체 제안은 `--verbose` 출력의 "고치는 방법 전체"에 있습니다.

`+` 줄에는 `-` 줄(원래 줄)의 들여쓰기를 붙입니다 (`+` 줄끼리의 상대 들여쓰기는 유지).

**줄 길이(E501)는 번호 붙은 지적이 아니라 화면 끝 "## 참고" 두 줄로만 보여 줍니다** (round3 이후 개선 ③). 줄 번호만 적고(15개가 넘으면 "외 N줄"), 수정안은 붙이지 않습니다.
- 스타일 이슈 섹션·맨 끝 지적 수·"다듬을 것" 수·HITL 항목 번호에서 빠집니다. E501만 있으면 스타일 이슈는 "(없음)"입니다.
- 추천 개선안의 줄 길이 항목은 최대 1개이고, 다른 지적이 있으면 넣지 않습니다. 요약 3줄은 "스타일: 줄 길이 초과 N줄."입니다.
- 게시용 코멘트 본문도 같은 형식이며, HITL에서 번호를 빼도 "참고"는 남습니다.
- **평가용 Review JSON에는 E501이 개별 지적으로 그대로 남고**, 탐지 판정·근거 확인 비율·근거-지적 일치율은 그 개별 지적으로 계산합니다.

줄별 길이와 코드 줄·주석·docstring 구분은 `--verbose`에서만 보입니다:

```
## 줄 길이 상세 (--verbose)
- app/module.py · 줄 길이 초과 9줄 (기준 88칸, 한글은 2칸) · 코드 줄 2, 주석·docstring 7
  코드 줄: 505 (95), 579 (99)
  주석·docstring: 495 (92), 496 (95), 508 (89), 535 (101), 563 (94), 566 (107), 571 (107)
나누는 방법: 괄호 안에서 줄을 바꾸거나, 긴 문자열·주석·docstring을 여러 줄로 나눕니다(줄마다 수정안은 붙이지 않음). 기준이 팀과 다르면 저장소 자체 ruff 설정(line-length)으로 조정하세요.
```

요약 5줄은 변경 내용 → 보안·버그 → 스타일 → 리뷰 범위·기준 → 가장 먼저 할 일 순서입니다.

| 줄 | 누가 쓰나 | 규칙 |
|---|---|---|
| 1. 변경 내용 | LLM (`SummaryDraft.change`) | diff로 확인되는 사실만. 추측(~같습니다·~로 보입니다)·평가(양호·적절·문제없음)·`langchain_classic` 권장 금지 |
| 2. 보안·버그 | 코드 (`secbug_line`) | 보안·버그 건수, 꼭 고칠 것(high·medium) 건수, 먼저 볼 위치. 인젝션 "참고"(low)는 세지 않음 |
| 3. 스타일 | 코드 (`style_line`) | 항상 한 줄. 줄 길이 초과는 줄 수로, 나머지는 ruff 코드로 |
| 4. 범위·기준 | 코드 (`scope_line`) | 리뷰한 파일, 건너뛴 파일, 적용 기준(줄 길이·변경 한도와 그 출처) |
| 5. 할 일 | LLM (`SummaryDraft.action`) | 지적 목록 안의 항목·줄 번호만. 꼭 고칠 것이 없으면 "꼭 고쳐야 할 것은 없습니다." |

LLM 줄(1·5)이 규칙을 어기면 1회 재생성하고, 그래도 어기면 **그 줄만** 코드 문장으로 바꿉니다(1줄: 파일별 추가/삭제 줄 수, 5줄: 가장 심각한 꼭 고칠 것 하나). 바뀐 줄은 `trace`의 `stage="summary"` 이벤트 `replaced`에 남습니다.

줄 길이 지적은 리뷰어가 내지 않고 코드가 ruff 결과로 만듭니다(길이 숫자는 ruff 값, 한글은 2칸). 기준은 맨 위 안내 줄과 요약 4줄에 보입니다. 저장소 폴더를 함께 주면(`--repo-dir`, GUI 커밋 모드) 그 저장소의 ruff 설정(`ruff.toml`·`.ruff.toml`·`pyproject.toml [tool.ruff]`)을 따르고, 변경 한도는 `--max-lines`·GUI 입력 > 저장소 `pyproject.toml [tool.citereview] max-lines` > 기본 300 순서이며 최대 500입니다.
