"""Gemini 호출 (임베딩·답변 생성). RAG_FAKE=1 이면 네트워크 없이 가짜 값을 돌려줍니다."""
from __future__ import annotations

import hashlib
import sys
import time
import json
import math
import re

from . import config

_client = None


def _gemini():
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY 가 없어요. .env 파일에 넣거나 RAG_FAKE=1 로 실행하세요.")
        from google import genai  # pip install google-genai
        from google.genai import types
        # 응답 대기 상한(밀리초)을 두고, 라이브러리 자체 재시도는 끈다(재시도는 아래 generate_json 이 직접 관리).
        opts = {"timeout": config.TIMEOUT_SEC * 1000}
        if hasattr(types, "HttpRetryOptions"):
            opts["retry_options"] = types.HttpRetryOptions(attempts=1)
        try:
            _client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(**opts))
        except (TypeError, ValueError):  # 라이브러리 버전이 옵션을 모르면 timeout 만
            _client = genai.Client(api_key=config.GEMINI_API_KEY,
                                   http_options=types.HttpOptions(timeout=config.TIMEOUT_SEC * 1000))
    return _client


# ------------------------------------------------------------------ 임베딩
def _fake_vec(text: str, dim: int) -> list[float]:
    """글자 2-gram 해시로 만든 가짜 벡터. 의미 검색은 못 하지만 파이프라인 확인용으로 충분."""
    v = [0.0] * dim
    t = re.sub(r"\s+", "", text)
    for i in range(len(t) - 1):
        h = int(hashlib.md5(t[i:i + 2].encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(texts: list[str], task: str) -> list[list[float]]:
    """task: RETRIEVAL_DOCUMENT(문단 저장할 때) / RETRIEVAL_QUERY(검색할 때)"""
    if config.FAKE:
        return [_fake_vec(t, config.EMBED_DIM) for t in texts]
    from google.genai import types
    out: list[list[float]] = []
    for i in range(0, len(texts), 100):  # 한 번에 최대 100개씩
        res = _gemini().models.embed_content(
            model=config.EMBED_MODEL,
            contents=texts[i:i + 100],
            config=types.EmbedContentConfig(task_type=task, output_dimensionality=config.EMBED_DIM),
        )
        out += [list(e.values) for e in res.embeddings]
    return out


# ------------------------------------------------------------------ 답변 생성
def _status(e: Exception) -> int | None:
    return getattr(e, "code", None) or getattr(e, "status_code", None)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def generate_json(system: str, user: str, fake_result: dict, *, model: str | None = None) -> dict:
    """JSON 으로만 답하게 해서 dict 로 돌려준다.
    - model 을 주면 그 모델을 기본 모델 대신 먼저 쓴다(예: OCR 분류). 없으면 GEMINI_CHAT_MODEL.
    - 한 번 호출은 최대 RAG_TIMEOUT_SEC 초까지만 기다린다.
    - 기본 모델은 과부하(503/429)·시간 초과면 3초 쉬고 1번 더, 그래도 안 되면 예비 모델을 1번씩.
    - 모델 이름 오류(404)·요청 형식 오류(400)는 재시도하지 않는다(기본 모델일 때)."""
    if config.FAKE:
        return fake_result
    from google.genai import types
    cfg = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json", temperature=0.2)
    last = None
    primary = model or config.CHAT_MODEL
    plan = [(primary, 0), (primary, 3)] + [(m, 0) for m in config.FALLBACK_MODELS if m != primary]
    for model, wait in plan:
        if wait:
            _log(f"  … {wait}초 후 다시 시도")
            time.sleep(wait)
        _log(f"  … {model} 응답 기다리는 중 (최대 {config.TIMEOUT_SEC}초)")
        t0 = time.time()
        try:
            res = _gemini().models.generate_content(model=model, contents=user, config=cfg)
            _log(f"  ✓ {model} 응답 {time.time() - t0:.1f}초")
            return json.loads(res.text)
        except json.JSONDecodeError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            code = _status(e)
            _log(f"  ✗ {model} 실패 {time.time() - t0:.1f}초: {code or type(e).__name__}")
            if model == primary and code in (400, 401, 403, 404):
                raise  # 이름·키 문제는 재시도해도 소용없음
            # 503·429·500·시간초과·네트워크 오류 → 다음 계획으로
    raise last
