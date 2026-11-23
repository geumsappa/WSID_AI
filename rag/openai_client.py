"""OpenAI 생성·임베딩 호출. RAG_FAKE=1이면 네트워크를 사용하지 않는다."""
from __future__ import annotations

import hashlib
import json
import math
import re

from openai import AsyncOpenAI, OpenAI
from app.config import settings
from . import config


def _options(timeout: float | None = None):
    """OpenAI 클라이언트 설정. Claude 를 쓸 때는 임베딩(검색)에만 쓰여서 OPENAI_API_KEY 를 써요."""
    if settings.llm_provider == 'anthropic':
        if not settings.openai_api_key:
            raise RuntimeError('임베딩 검색에는 OPENAI_API_KEY 가 필요해요.')
        return dict(api_key=settings.openai_api_key, timeout=timeout or config.TIMEOUT_SEC, max_retries=0)
    if not settings.llm_api_key:
        raise RuntimeError('LLM_API_KEY를 설정해 주세요.')
    return dict(api_key=settings.llm_api_key, base_url=settings.llm_base_url or None,
                timeout=timeout or config.TIMEOUT_SEC, max_retries=0)


def has_embeddings() -> bool:
    """실제 임베딩 검색이 가능한지 (아니면 글자 겹침 검색으로 대신해요)."""
    return config.FAKE or settings.llm_provider != 'anthropic' or bool(settings.openai_api_key)


def _client(timeout: float | None = None):
    return OpenAI(**_options(timeout))


def async_client():
    return AsyncOpenAI(**_options())


def _fake_vec(text: str, dim: int) -> list[float]:
    v = [0.0] * dim
    text = re.sub(r'\s+', '', text)
    for i in range(len(text) - 1):
        h = int(hashlib.md5(text[i:i + 2].encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(texts: list[str], task: str) -> list[list[float]]:
    if task not in ('RETRIEVAL_DOCUMENT', 'RETRIEVAL_QUERY'):
        raise ValueError('invalid retrieval task')
    if config.FAKE:
        return [_fake_vec(text, config.EMBED_DIM) for text in texts]
    vectors = []
    client = _client()
    try:
        for offset in range(0, len(texts), 100):
            response = client.embeddings.create(model=config.EMBED_MODEL,
                input=texts[offset:offset + 100], dimensions=config.EMBED_DIM)
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda x: x.index))
    finally:
        client.close()
    return vectors


def generate_json(system: str, user: str, fake_result: dict, *,
                  model: str | None = None, timeout: float | None = None) -> dict:
    """model을 주면 그 모델, 없으면 LLM_MODEL. timeout을 주면 RAG_TIMEOUT_SEC 대신 사용."""
    if config.FAKE:
        return fake_result
    model = model or settings.llm_model
    if not model:
        raise RuntimeError('LLM_MODEL을 설정해 주세요.')
    if settings.llm_provider == 'anthropic':
        from app.llm.llm_client import anthropic_text, parse_json
        return parse_json(anthropic_text(system, user, model=model, timeout=timeout or config.TIMEOUT_SEC))
    client = _client(timeout)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            response_format={'type': 'json_object'},
        )
        result = json.loads(response.choices[0].message.content or '')
        if not isinstance(result, dict):
            raise ValueError('모델 응답은 JSON 객체여야 합니다.')
        return result
    finally:
        client.close()
