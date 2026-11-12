# deprecated API 매핑표

> RAG 코퍼스 문서입니다. **표의 행 하나가 청크 하나**이며, 첫 열의 `rule_id`(`DEP-<API명>`)를 청크 메타데이터로 붙입니다.
> 교육 환경 설치본(langchain 1.4.7, langgraph 1.2.19)에서 직접 import해 확인한 항목입니다.
> `issue_type` 분류(제거되어 실행 실패 → `bug`, 경고 후 동작 → `style`)는 [SERVICE.md](../../SERVICE.md) 1번 규칙을 따릅니다.
> **출처 링크 규칙:** LangChain v1 마이그레이션 가이드를 실제로 열어 해당 API가 언급된 것을 확인한 링크만 적습니다(2026-11-11 확인, 앵커도 페이지에 있는 것 확인). 언급을 찾지 못한 행은 "링크 확인 필요"로 둡니다.

| 규칙 ID | 구버전 사용 | 1.x 상태 | 대체 | 분류 | 출처 |
|---|---|---|---|---|---|
| `DEP-create_react_agent` | `from langgraph.prebuilt import create_react_agent` | 경고 후 동작 ("`langchain.agents`로 이동") | `from langchain.agents import create_agent` | style | https://docs.langchain.com/oss/python/migrate/langchain-v1#import-path |
| `DEP-LLMChain` | `from langchain.chains import LLMChain` | **제거** (ImportError), `langchain_classic`으로 이동 | LCEL `prompt \| model \| parser` | bug | https://docs.langchain.com/oss/python/migrate/langchain-v1#langchain-classic |
| `DEP-initialize_agent` | `from langchain.agents import initialize_agent` | **제거**, `langchain_classic`으로 이동 | `create_agent` | bug | 링크 확인 필요 |
| `DEP-AgentExecutor` | `from langchain.agents import AgentExecutor` | **제거**, `langchain_classic`으로 이동 | `create_agent` | bug | 링크 확인 필요 |
| `DEP-memory` | `from langchain.memory import ConversationBufferMemory` 등 | **제거**, `langchain_classic`으로 이동 | 체크포인터 + `thread_id` | bug | 링크 확인 필요 |
| `DEP-retrievers` | `from langchain.retrievers import ...` | **제거** | 각 통합 패키지의 retriever (예: `langchain_chroma`의 `vectorstore.as_retriever()`) | bug | https://docs.langchain.com/oss/python/migrate/langchain-v1#langchain-classic |
| `DEP-langchain_classic` | `langchain_classic.*` 사용 | 동작함 (호환용 패키지) | 위 1.x 대체 API | style | https://docs.langchain.com/oss/python/migrate/langchain-v1#langchain-classic |
