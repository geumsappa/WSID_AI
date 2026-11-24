"""Shared evidence contract for intake readiness and scenario generation."""
from app.schemas.fast_discovery import ReadinessBasis

CONTEXT_TOPICS = {"ACTIVITY_DETAIL", "PURPOSE", "ACCESS", "INTERACTION", "TRANSACTION", "ROLE"}


def _overlap(left, right):
    left, right = " ".join(left.split()), " ".join(right.split())
    return bool(left and right and (left in right or right in left))


def basis_supports_context(basis, topics):
    if basis is None or not basis.contactReason.strip():
        return False
    if not all(topics.get(topic, {}).get("status") == "KNOWN" for topic in basis.topics):
        return False
    return any(
        topic in CONTEXT_TOPICS and any(
            quote.turn == topics[topic].get("turn") and _overlap(quote.quote, topics[topic].get("quote", ""))
            for quote in basis.evidence)
        for topic in basis.topics)


def load_basis(value, messages, topics):
    if value is None:
        return None  # Historical/direct API callers have no intake decision.
    basis = ReadinessBasis.model_validate(value)
    source = {row["turn"]: row["text"] for row in messages}
    if not basis_supports_context(basis, topics) or any(
        " ".join(quote.quote.split()) not in " ".join(source.get(quote.turn, "").split())
        for quote in basis.evidence):
        raise ValueError("Readiness basis must reference confirmed user context")
    return basis


def plan_uses_basis(graph, plan, basis):
    if basis is None:
        return True
    evidence = {item.id: item for item in graph.evidence}
    return any(
        recorded.sourceTurn == quote.turn and _overlap(recorded.quote, quote.quote)
        for item in plan.personalizationBasis
        if (recorded := evidence.get(item.evidenceId)) is not None
        for quote in basis.evidence)


def interaction_candidate(topics, target):
    """A confirmed interaction can be planned before inventing more intake needs.

    This is evidence for a candidate only: the independent plan review still
    decides whether that interaction supports a plausible fictional approach.
    """
    identity = {"PERSON": ("PEOPLE", "PERSON_NAME"), "SERVICE": ("SERVICE",)}.get(target)
    if not identity or any(topics.get(key, {}).get("status") != "KNOWN"
                           for key in ("ACTIVITY", "INTERACTION", *identity)):
        return None
    interaction = topics["INTERACTION"]
    return ReadinessBasis(topics=["INTERACTION"],
        evidence=[{"turn": interaction["turn"], "quote": interaction["quote"]}],
        contactReason="확인된 상호작용 ‘" + interaction["quote"][:100] +
                      "’을 언급하는 후속 연락을 계획한다. 새 연락은 가상 설정이며 실제 경험에 추가하지 않는다.")
