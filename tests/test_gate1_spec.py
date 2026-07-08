"""게이트 1(스펙 적합성) 자동 검증. server/main.py 소스를 파싱해 하드 규칙을 강제한다.

fastmcp 없이도 돌도록 소스 정적 분석 방식을 쓴다.
- 툴명: 영문/숫자/underscore/hyphen, ≤128자, 중복 없음
- description: ≤1,024자, "주식사전 퀴즈" 병기
- "kakao" 문자열이 서버명/툴명/description에 없음(대소문자 무관)
- annotations 5필드(title/readOnlyHint/destructiveHint/openWorldHint/idempotentHint) 명시
"""

from __future__ import annotations

import re
from pathlib import Path

_MAIN = Path(__file__).resolve().parent.parent / "server" / "main.py"
_SRC = _MAIN.read_text(encoding="utf-8")

_TOOL_RE = re.compile(
    r'name="([A-Za-z0-9_-]+)",\s*description=\((.*?)\),\s*annotations', re.S
)

EXPECTED_TOOLS = {
    "quiz",
    "submit_answer",
}


def _descriptions() -> dict[str, str]:
    out = {}
    for m in _TOOL_RE.finditer(_SRC):
        name = m.group(1)
        desc = "".join(re.findall(r'"([^"]*)"', m.group(2)))
        out[name] = desc
    return out


def test_expected_tools_registered():
    tools = set(_descriptions())
    assert tools == EXPECTED_TOOLS


def test_tool_names_valid_and_unique():
    names = [m.group(1) for m in _TOOL_RE.finditer(_SRC)]
    assert len(names) == len(set(names))  # 중복 없음
    for n in names:
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,128}", n), n


def test_descriptions_within_limit_and_labeled():
    for name, desc in _descriptions().items():
        assert 0 < len(desc) <= 1024, f"{name} description 길이 위반"
        assert "주식사전 퀴즈" in desc, f"{name} 병기 누락"


def test_no_kakao_anywhere():
    assert "kakao" not in _SRC.lower()
    # 서버명도 확인
    assert "kakao" not in re.search(r'FastMCP\(name="([^"]+)"', _SRC).group(1).lower()


def test_all_annotations_present():
    for field in (
        "readOnlyHint",
        "destructiveHint",
        "openWorldHint",
        "idempotentHint",
    ):
        assert field in _SRC, f"annotation {field} 누락"
    assert _SRC.count("title=") >= len(EXPECTED_TOOLS)  # 툴별 title 명시
