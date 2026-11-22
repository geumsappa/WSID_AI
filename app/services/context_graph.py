"""Structural/evidence checks; no food, job, payer or activity keyword branches."""
import hashlib
import json

from app.schemas.context_graph import ContextGraph, ScenarioPlan


class ContextIntegrityError(ValueError):
    pass


def _unique(items):
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise ContextIntegrityError("duplicate_id")
    return set(ids)


def check_graph(graph: ContextGraph, messages, previous=None):
    entities = _unique(graph.entities)
    events = _unique(graph.events)
    evidence = _unique(graph.evidence)
    people = {entity.id: entity for entity in graph.entities}
    if graph.userActorId not in entities or people[graph.userActorId].kind != "USER":
        raise ContextIntegrityError("missing_user")
    if sum(entity.kind == "USER" for entity in graph.entities) != 1:
        raise ContextIntegrityError("ambiguous_user")
    if previous and graph.userActorId != previous.userActorId:
        raise ContextIntegrityError("changed_user_identity")
    sources = {message["turn"]: " ".join(message["text"].split()) for message in messages}
    trusted = {(item.id, item.sourceTurn, item.quote) for item in previous.evidence} if previous else set()
    for quote in graph.evidence:
        source = sources.get(quote.sourceTurn)
        if source is not None:
            if " ".join(quote.quote.split()) not in source:
                raise ContextIntegrityError("unsupported_quote")
        elif (quote.id, quote.sourceTurn, quote.quote) not in trusted:
            raise ContextIntegrityError("unknown_source_turn")
    for event in graph.events:
        if event.actorId is not None and event.actorId not in entities:
            raise ContextIntegrityError("unknown_actor")
        if not set(event.participantIds).issubset(entities) or not set(event.evidenceIds).issubset(evidence):
            raise ContextIntegrityError("unknown_event_reference")
    if not set(graph.focusEventIds).issubset(events):
        raise ContextIntegrityError("unknown_focus")
    if len(json.dumps(graph.model_dump(), ensure_ascii=False)) > 6500:
        raise ContextIntegrityError("context_too_large")


def check_claims(claims, graph):
    events = {event.id: event for event in graph.events}
    seen = set()
    for claim in claims:
        event = events.get(claim.eventId)
        if event is None or event.certainty != "CONFIRMED":
            raise ContextIntegrityError("unconfirmed_anchor")
        if event.actorId != claim.actorId:
            raise ContextIntegrityError("changed_actor")
        if claim.eventId in seen:
            raise ContextIntegrityError("duplicate_claim")
        seen.add(claim.eventId)
    if not graph.focusEventIds or not set(graph.focusEventIds).issubset(seen):
        raise ContextIntegrityError("unrelated_focus")


def check_plan(plan: ScenarioPlan, graph):
    from app.scenario_data import ALLOWED_CHANNELS
    check_claims(plan.anchorClaims, graph)
    if not plan.personalizationBasis:
        raise ContextIntegrityError("missing_personalization_basis")
    events = {event.id: event for event in graph.events}
    anchors = {claim.eventId for claim in plan.anchorClaims}
    for basis in plan.personalizationBasis:
        event = events.get(basis.eventId)
        if event is None or basis.evidenceId not in event.evidenceIds:
            raise ContextIntegrityError("unsupported_personalization_basis")
        if basis.eventId not in anchors:
            raise ContextIntegrityError("personalization_event_missing_from_anchor_claims")
    entities = {entity.id for entity in graph.entities}
    if plan.recipientActorId != graph.userActorId:
        raise ContextIntegrityError("wrong_training_recipient")
    if plan.impersonatedActorId is not None and plan.impersonatedActorId not in entities:
        raise ContextIntegrityError("unknown_impersonated_actor")
    if plan.channel not in ALLOWED_CHANNELS:
        raise ContextIntegrityError("unsupported_channel")


def fingerprint(graph):
    raw = json.dumps(graph.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Letter-only machine digest cannot be mistaken for account/phone numbers by text masking.
    return hashlib.sha256(raw.encode()).hexdigest().translate(str.maketrans("0123456789", "ghijklmnop"))


def project_facts(graph):
    labels = {entity.id: entity.label for entity in graph.entities}
    evidence = {item.id: item for item in graph.evidence}
    selected = [event for event in graph.events if event.id in graph.focusEventIds]
    return [{"category": event.category, "detail": (f"{labels.get(event.actorId, '주체 미확인')}: {event.action}" + (f" / {event.object}" if event.object else ""))[:200],
             "factId": event.id, "sourceTurn": min(evidence[key].sourceTurn for key in event.evidenceIds),
             "actorId": event.actorId, "eventId": event.id, "evidenceIds": event.evidenceIds,
             "certainty": event.certainty, "channel": None, "impersonatedIdentity": None,
             "requestedAction": None, "riskFactors": []} for event in selected[:6]]
