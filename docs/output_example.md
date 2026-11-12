# 출력 예시 (기준 화면)

> 터미널 기본 출력의 기준입니다. 렌더러 구현과 리뷰는 이 화면에 맞춥니다. 내용은 교육용 더미 데이터입니다.
> 출력 구성은 [SERVICE.md](../SERVICE.md) 1번, 렌더링 구현 규칙은 [CLAUDE.md](../CLAUDE.md) 6번 "출력 렌더링"을 따릅니다. 내부 로그는 `--verbose`에서만 나옵니다.

```
CiteReview · data/eval_diffs/03_llmchain.diff
리뷰: app/chain.py, config.py · 건너뜀: README.md (.py 아님) · 마스킹: 비밀값 1건
꼭 고쳐야 할 것 2건 (보안 1, 버그 1)

## 요약
1. app/chain.py의 LLMChain 기반 체인을 정리하는 변경입니다.
2. 1.x에서 제거된 langchain.chains import가 남아 있어 실행하면 바로 실패합니다.
3. config.py에 AWS 키가 그대로 들어 있어 마스킹했습니다.
4. 줄 길이 초과 1건과 쓰지 않는 import 1건이 있습니다.
5. LCEL로 바꾸고 키를 환경변수로 옮기면 모두 해결됩니다.

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

3. app/chain.py:27 · low
   한 줄이 95자로 88자 제한을 넘습니다. 긴 줄은 diff와 리뷰 화면에서 읽기 어렵습니다.
      26 | def build_chain(model):
    > 27 |     prompt = ChatPromptTemplate.from_template("다음 코드를 리뷰하고 문제점을 근거와 함께 설명해 주세요: {code}")
      28 |     return prompt | model
   근거: PEP 8 최대 줄 길이 (PEP8-maximum-line-length), ruff E501 (ruff:E501)
   고치는 방법: 템플릿 문자열을 괄호 안에서 두 줄로 나눕니다.

4. app/chain.py:3 · low
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

지적 4건 (꼭 고칠 것 2 · 다듬을 것 2) · 근거가 확인되지 않아 제외된 지적 1건 (--verbose로 확인)
```

## 문제없는 diff (교육용 더미 데이터입니다)

지적이 하나도 없으면 맨 위 안내 줄 바로 다음에 "문제를 찾지 못했습니다."를 한 줄 보여 주고, 빈 소제목은 "(없음)"으로 둡니다.

```
CiteReview · data/eval_diffs/10_clean_refactor.diff
리뷰: app/utils.py · 건너뜀: 없음 · 마스킹: 없음
꼭 고쳐야 할 것 없음
문제를 찾지 못했습니다.

## 요약
1. app/utils.py에서 공백 정리 로직을 _clean 함수로 추출한 리팩터링입니다.
2. normalize와 unique_tags의 동작은 바뀌지 않았습니다.
3. 타입 힌트가 추가되어 함수의 입력과 출력이 분명해졌습니다.
4. ruff 검사에서 변경된 줄의 위반이 없습니다.
5. 고칠 문제를 찾지 못했습니다.

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
