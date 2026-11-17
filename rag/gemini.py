"""Gemini 호출 (임베딩·답변 생성). RAG_FAKE=1 이면 네트워크 없이 가짜 값을 돌려줍니다."""
from __future__ import annotations

import hashlib
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
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
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
def generate_json(system: str, user: str, fake_result: dict) -> dict:
    """JSON 으로만 답하게 해서 dict 로 돌려준다."""
    if config.FAKE:
        return fake_result
    from google.genai import types
    res = _gemini().models.generate_content(
        model=config.CHAT_MODEL,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    return json.loads(res.text)
