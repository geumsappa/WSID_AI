"""사례 DB(app/data/fraud_cases.yaml)를 읽어오는 곳 (SIM-005/006).

- 시나리오 생성 프롬프트에 사례 요약을 넣어 "근거 있는" 시나리오를 만들게 하고
- 검증 단계에서 시나리오가 단 caseId 가 실제로 DB 에 있는지, 유형이 맞는지 확인합니다.
"""
from functools import lru_cache
from pathlib import Path

import yaml

CASES_PATH = Path(__file__).resolve().parent / "data" / "fraud_cases.yaml"


@lru_cache
def load_cases() -> dict[str, dict]:
    """{caseId: 사례} 형태로 돌려준다. 서버가 켜질 때 한 번만 읽음."""
    data = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8")) or {}
    return {c["id"]: c for c in data.get("cases", [])}


def get_case(case_id: str) -> dict | None:
    return load_cases().get(case_id)


def case_ids_for_subtype(subtype: str) -> list[str]:
    return [cid for cid, c in load_cases().items() if c.get("subtype") == subtype]


def cases_for_prompt() -> str:
    """LLM 에 보여줄 사례 목록. 출처·실존 기관명은 빼고 수법 요약만 보낸다."""
    lines = []
    for cid, c in load_cases().items():
        info = ", ".join(c.get("requestedInfo") or []) or "없음"
        lines.append(
            f"- {cid} [{c['riskType']} / {c['subtype']}] {c['summary']} "
            f"(수법: {', '.join(c.get('tactics') or [])} | 요구 정보: {info})"
        )
    return "\n".join(lines)


def public_ref(case: dict) -> dict:
    """화면·리포트에 보여줄 근거 사례 정보."""
    return {
        "caseId": case["id"],
        "title": case["title"],
        "issuer": case.get("issuer") or "",
        "alertNo": case.get("alertNo"),
        "sourceHost": case.get("sourceHost") or "",
        "sourceDate": case.get("sourceDate"),
        "sourceUrl": case.get("sourceUrl") or "",
    }
