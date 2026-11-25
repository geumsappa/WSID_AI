"""Langfuse observations for the assist API, using explicit metadata only."""
from contextvars import ContextVar
from functools import lru_cache
import json
import logging
import os
from uuid import UUID

from app.config import settings
from rag import config

from .contracts import AnswerRequest, AnswerResponse, PlanRequest, PlanResponse

logger = logging.getLogger("uvicorn.error.assist")
_observation = ContextVar("assist_observation", default=None)


@lru_cache(maxsize=1)
def _public_reference_catalog() -> dict[str, dict[str, str]]:
    # Resolve titles and URLs locally so request or model text is never used as a document label.
    try:
        with config.REFERENCES_PATH.open(encoding="utf-8") as source:
            references = [json.loads(line) for line in source if line.strip()]
    except (OSError, ValueError):
        logger.warning("assist Langfuse document catalog could not be loaded")
        return {}
    documents = {}
    for reference in references:
        if not isinstance(reference, dict):
            continue
        code, title = reference.get("code"), reference.get("title")
        if not isinstance(code, str) or not isinstance(title, str):
            continue
        document = {"code": code, "title": title}
        url = reference.get("source_url") or reference.get("mirror_url")
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            document["source_url"] = url
        documents[code] = document
    return documents


def _documents(codes: list[str]) -> list[dict[str, str]]:
    if not codes:
        return []
    catalog = _public_reference_catalog()
    return [catalog[code] for code in codes if code in catalog]


@lru_cache(maxsize=1)
def _client():
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    if not public_key or not secret_key:
        logger.warning("assist Langfuse credentials are missing")
        return None
    try:
        from langfuse import Langfuse
        from opentelemetry.sdk.trace import TracerProvider

        # Keep framework and provider spans out of this exporter.
        return Langfuse(public_key=public_key, secret_key=secret_key, tracer_provider=TracerProvider())
    except Exception:
        logger.warning("assist Langfuse initialization failed")
        return None


def begin_trace(request: PlanRequest | AnswerRequest, request_id: UUID):
    if not config.ASSIST_LANGFUSE_ENABLED:
        return None
    client = _client()
    if client is None:
        return None
    try:
        metadata = {
            "request_id": str(request_id),
            "phase": request.phase,
            "provider": "fake" if config.FAKE else settings.llm_provider,
            "model": "fake" if config.FAKE else config.ASSIST_LLM_MODEL or settings.llm_model,
        }
        if isinstance(request, AnswerRequest):
            codes = [ref.code for ref in request.verifiedReferences]
            metadata["verified_reference_codes"] = codes
            metadata["verified_documents"] = _documents(codes)
        observation = client.start_observation(
            name=f"assist.{request.phase.lower()}",
            trace_context={"trace_id": client.create_trace_id(seed=str(request_id))},
            metadata=metadata,
        )
        return _observation.set(observation)
    except Exception:
        logger.warning("assist Langfuse observation could not be started")
        return None


def record(stage: str, *, document_codes: list[str] | None = None, **metadata):
    parent = _observation.get()
    if parent is None:
        return
    try:
        output = {"documents": _documents(document_codes)} if document_codes is not None else None
        parent.start_observation(name=f"assist.{stage}", metadata=metadata, output=output).end()
    except Exception:
        logger.warning("assist Langfuse stage could not be recorded")


def finish_trace(token, status: str, error: str, result: PlanResponse | AnswerResponse | None):
    if token is None:
        return
    observation = _observation.get()
    _observation.reset(token)
    try:
        output = {"status": status, "reason": result.reasonCode if result is not None else error}
        if isinstance(result, PlanResponse):
            output["candidate_references"] = [
                {"code": ref.code, "version": ref.version} for ref in result.candidateReferences
            ]
        if isinstance(result, AnswerResponse):
            output["cited_reference_codes"] = result.referenceCodes
            output["cited_documents"] = _documents(result.referenceCodes)
        try:
            observation.update(output=output, level="ERROR" if status == "FAILED" else "DEFAULT")
        finally:
            observation.end()
    except Exception:
        logger.warning("assist Langfuse observation could not be completed")
