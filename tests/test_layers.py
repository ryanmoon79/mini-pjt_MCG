"""src/ 도메인 import 방향 테스트 (CLAUDE.md 2번 "파일 배치 규칙").

다른 도메인은 자기보다 **아래층만** import합니다(같은 도메인 안은 자유).
맨 위의 제출 규약 재노출 파일(src/agent.py, src/tools.py, src/retriever.py)은
검사하지 않습니다.
실제 LLM이나 모듈 import 없이, 파일을 ast로 읽어 import 문만 봅니다.
"""

import ast

from src.shared import config

# 층 번호가 클수록 위층 (위층 → 아래층 import만 허용)
LAYERS = {
    "interfaces": 5,
    "review": 4,
    "guardrails": 3,
    "report": 3,
    "evidence": 2,
    "toolkit": 2,
    "diff": 1,
    "observability": 1,
    "findings": 1,
    "shared": 0,
}
FACADES = {"agent.py", "retriever.py", "tools.py"}  # src/ 바로 아래 재노출 파일


def imported_domains(tree: ast.AST) -> list[tuple[int, str]]:
    """(줄 번호, 대상 도메인) — `from src.X…`, `from src import X`, `import src.X`."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] != "src":
                continue
            if len(parts) == 1:  # from src import X
                out += [(node.lineno, a.name) for a in node.names]
            else:
                out.append((node.lineno, parts[1]))
        elif isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                if parts[0] == "src" and len(parts) > 1:
                    out.append((node.lineno, parts[1]))
    return out


def violations(source_domain: str, tree: ast.AST) -> list[str]:
    """이 파일(도메인)이 어기는 import 목록."""
    bad = []
    for line, target in imported_domains(tree):
        if target == source_domain:
            continue
        if target not in LAYERS:
            bad.append(f"{line}행: 등록되지 않은 도메인 {target}")
        elif LAYERS[target] >= LAYERS[source_domain]:
            bad.append(
                f"{line}행: {source_domain}(층 {LAYERS[source_domain]}) → "
                f"{target}(층 {LAYERS[target]})"
            )
    return bad


def test_src_imports_only_go_down_the_layers():
    src = config.ROOT_DIR / "src"
    problems = []
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src)
        if len(rel.parts) == 1:
            assert rel.name in FACADES, f"src/ 바로 아래에는 재노출 파일만: {rel}"
            continue
        domain = rel.parts[0]
        assert domain in LAYERS, f"층에 등록되지 않은 도메인 폴더: {domain}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        problems += [f"src/{rel.as_posix()} {v}" for v in violations(domain, tree)]
    assert problems == [], "\n".join(problems)
    # 검사기 자체 확인: 거꾸로 가는 import와 같은 층 import는 잡아야 함
    assert violations("toolkit", ast.parse("from src.review.graph import run_query"))
    assert violations("report", ast.parse("from src.guardrails import input"))
    ok = ast.parse("from src.findings.schema import Finding")
    assert not violations("review", ok)
