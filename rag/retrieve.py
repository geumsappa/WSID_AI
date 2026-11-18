"""사실(facts) → 관련 근거 문단 찾기 → AI-07 요청의 verifiedReferences 형식으로 반환.

1) 사실이 YES 인 항목을 피해 상황 코드로 바꿔 메타데이터 필터
2) 사실로 만든 질의문을 임베딩해서 유사도 정렬
3) 대응 절차(procedure)·법적 절차(legal) 문단에 가산점
4) 최대 k개(기본 5, 계약 상한 10)
"""
from __future__ import annotations

import numpy as np

from .config import MAX_REFERENCES
from .facts import query_text, situations_for, validate_facts
from .gemini import embed
from .index import load

# 통신사기피해환급법(지급정지·환급) 절차 문단. 중고거래 등 재화·용역 사기는 법 적용 대상이 아니므로
# 이 문단들을 보내면 모델이 '환급받을 수 있다'고 틀리게 안내한다 → 해당 유형에서는 아예 제외.
# (AI-07 요청에는 사기 유형이 없어서 설명 단계에서는 구분할 방법이 없음. 근거를 고르는 단계에서 막아야 함)
REFUND_ONLY_PREFIXES = (
    "REF_LAW_REFUND_PROCEDURE_V1_P1",   # 환급 절차
    "REF_FSS_2023_06_GUIDE_V1_P2",      # 지급정지 후 사건사고사실확인원 제출
    "REF_FSC_2023_11_FACE_",            # 대면편취형 피해구제
    "REF_LAW_2026_AMEND_",              # 환급법 개정
    "REF_FSC_2026_10_ALERT_V1_P4",      # 지급정지 후 14일 내 피해구제 신청서
)
NON_TELECOM_TYPES = {"SECONDHAND_TRADE"}

KIND_BONUS = {"procedure": 0.08, "legal": 0.05, "prevention": 0.03, "case": 0.0, "general": 0.0, "contact": 0.0}


def find_references(facts: dict, fraud_type: str | None = None, k: int = 5, debug: bool = False) -> list[dict]:
    facts = validate_facts(facts)
    k = min(k, MAX_REFERENCES)
    vecs, refs, _ = load()
    wanted = set(situations_for(facts))
    if not wanted:  # 확정된 피해 사실이 없으면 근거를 찾지 않음 (Spring 정책: 추가 질문 우선)
        return []

    q = np.array(embed([query_text(facts, fraud_type)], task="RETRIEVAL_QUERY")[0], dtype=np.float32)
    q /= np.linalg.norm(q) + 1e-12
    sims = vecs @ q

    scored = []
    for i, r in enumerate(refs):
        overlap = wanted & set(r.get("situation_codes", []))
        if not overlap:
            continue
        if fraud_type in NON_TELECOM_TYPES and r["code"].startswith(REFUND_ONLY_PREFIXES):
            continue
        if fraud_type and fraud_type in r.get("fraud_types", []):
            penalty = -0.12  # 유형이 일치하는 문단은 가산점
        elif fraud_type and r.get("fraud_types") and fraud_type not in r["fraud_types"]:
            # 유형이 명시돼 있고 다르면 감점(제외는 아님: 공통 절차 문단이 많음)
            penalty = 0.05
        else:
            penalty = 0.0
        # 중고거래 사기에 환급법 절차 문단만 붙지 않게: '적용 대상과 제외' 문단은 함께 올림
        bonus = KIND_BONUS.get(r.get("content_kind") or "general", 0) + 0.02 * len(overlap)
        if fraud_type == "SECONDHAND_TRADE" and r.get("content_kind") == "legal":
            bonus += 0.1
        scored.append((float(sims[i]) + bonus - penalty, r))
    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    for score, r in scored[:k]:
        item = {"code": r["code"], "title": r["title"], "excerpt": r["excerpt"]}
        if debug:
            item["_score"] = round(score, 4)
            item["_reviewed"] = r.get("reviewed", False)
        out.append(item)
    return out


def find_by_question(question: str, fraud_type: str | None = None, k: int = 5) -> list[dict]:
    """확정된 피해 사실이 없을 때(전부 NO/UNKNOWN) 질문 문장으로 근거를 찾는다. (질문 답변 API 전용)
    중고거래 등 환급법 대상이 아닌 유형이면 지급정지·환급 절차 문단은 빼요."""
    k = min(k, MAX_REFERENCES)
    vecs, refs, _ = load()
    text = f"{question}. 사기 유형 {fraud_type}" if fraud_type else question
    q = np.array(embed([text], task="RETRIEVAL_QUERY")[0], dtype=np.float32)
    q /= np.linalg.norm(q) + 1e-12
    sims = vecs @ q
    scored = []
    for i, r in enumerate(refs):
        if fraud_type in NON_TELECOM_TYPES and r["code"].startswith(REFUND_ONLY_PREFIXES):
            continue
        bonus = KIND_BONUS.get(r.get("content_kind") or "general", 0)
        if fraud_type and fraud_type in r.get("fraud_types", []):
            bonus += 0.12
        scored.append((float(sims[i]) + bonus, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"code": r["code"], "title": r["title"], "excerpt": r["excerpt"]} for _, r in scored[:k]]
