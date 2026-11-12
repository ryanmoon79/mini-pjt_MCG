"""tests/ 공통 설정: 프로젝트 루트를 import 경로에 넣어 `src.*`를 불러올 수 있게 합니다.

`tests/`의 테스트는 실제 LLM을 호출하지 않습니다 (CLAUDE.md 6번).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
