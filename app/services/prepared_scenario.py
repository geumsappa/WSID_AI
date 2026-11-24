"""Compact, authenticated receipt of a plan approved during discovery."""
import base64
import hashlib
import hmac
import json
import zlib

from app.config import settings
from app.schemas.context_graph import ContextGraph, ScenarioPlan, SemanticReview
from app.services.context_graph import ContextIntegrityError, check_graph, check_plan

MAX_BYTES = 65536
MAX_TOKEN = 14000


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def source_hash(messages, topics, requirements, basis):
    return hashlib.sha256(_json([messages, topics, requirements, basis])).hexdigest()


def _sign(data):
    if not settings.internal_api_key:
        raise ContextIntegrityError("missing_plan_signing_key")
    return hmac.new(settings.internal_api_key.encode(), data.encode(), hashlib.sha256).hexdigest()


def seal(graph, plan, review, *, messages, topics, requirements, basis):
    if not review.accepts() or (plan.referenceUses and not review.referencesSupported):
        raise ContextIntegrityError("unapproved_context_plan")
    payload = {"version": 1, "sourceHash": source_hash(messages, topics, requirements, basis),
        "context": graph.model_dump(), "plan": plan.model_dump(),
        "review": review.model_dump(), "retrieval": plan._retrieval_trace}
    raw = _json(payload)
    if len(raw) > MAX_BYTES:
        raise ContextIntegrityError("prepared_plan_too_large")
    data = base64.urlsafe_b64encode(zlib.compress(raw)).decode()
    token = data + "." + _sign(data)
    if len(token) > MAX_TOKEN:
        raise ContextIntegrityError("prepared_plan_too_large")
    return token


def restore(token, *, messages, topics, requirements, basis):
    try:
        if not isinstance(token, str) or len(token) > MAX_TOKEN:
            raise ValueError("invalid receipt")
        data, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, _sign(data)):
            raise ValueError("invalid signature")
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(data, altchars=b"-_", validate=True), MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError("invalid compressed plan")
        payload = json.loads(raw)
        if payload["version"] != 1 or payload["sourceHash"] != source_hash(messages, topics, requirements, basis):
            raise ValueError("changed source")
        review = SemanticReview.model_validate(payload["review"])
        if not review.accepts():
            raise ValueError("unapproved plan")
        graph = ContextGraph.model_validate(payload["context"])
        plan = ScenarioPlan.model_validate(payload["plan"])
        check_graph(graph, messages)
        check_plan(plan, graph)
        from app.services.scenario_retrieval import validate_uses
        plan._retrieval_trace = payload["retrieval"]
        validate_uses(plan.referenceUses, plan._retrieval_trace)
        if plan.referenceUses and not review.referencesSupported:
            raise ValueError("unapproved references")
        plan._context_approved = True
        plan._review_messages = [dict(item) for item in messages]
        plan._readiness_basis = basis
        return graph, plan
    except (ValueError, TypeError, KeyError, zlib.error):
        raise ContextIntegrityError("invalid_or_stale_prepared_plan") from None
