"""LLM 호출을 한 곳에 모아둔 파일.

다른 코드는 generate_json() 하나만 부르면 됩니다.
LLM 공급자를 바꾸고 싶으면 .env 의 LLM_PROVIDER 만 바꾸면 되고, 다른 파일은 손댈 필요가 없습니다.
"""
import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from app.config import settings
from app.llm import mock_llm

logger = logging.getLogger(__name__)


_generation_deadline = ContextVar("generation_deadline", default=None)


@contextmanager
def generation_budget(seconds):
    """One wall-clock budget shared by all model stages and retries in a request."""
    previous = _generation_deadline.get()
    deadline = time.monotonic() + seconds
    token = _generation_deadline.set(min(deadline, previous) if previous is not None else deadline)
    try:
        yield
    finally:
        _generation_deadline.reset(token)


class LLMError(Exception):
    """LLM 호출 또는 응답 해석에 실패했을 때 쓰는 에러."""


def generate_json(system_prompt: str, user_content: str, *, max_tokens: int = 800,
                  timeout: float | None = None, attempts: int = 2, reasoning_effort: str | None = None, reuse_connection: bool = False,
                  model: str | None = None) -> dict:
    """LLM 에게 묻고, 응답을 JSON(dict)으로 바꿔서 돌려준다.

    attempts 번까지 시도하고, 그래도 안 되면 LLMError 를 던진다.
    (그러면 호출한 쪽에서 기본 문구로 대체)
    max_tokens: 답변 최대 길이 / timeout: 이번 호출만 다른 대기시간을 쓰고 싶을 때
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            call_timeout = timeout or settings.llm_timeout_seconds
            deadline = _generation_deadline.get()
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMError("generation_deadline_exceeded")
                call_timeout = min(call_timeout, remaining)
            raw_text = _call_provider(system_prompt, user_content, max_tokens,
                                      call_timeout, reasoning_effort=reasoning_effort, reuse_connection=reuse_connection,
                                      model=model)
            return parse_json(raw_text)
        except Exception as e:  # 네트워크 오류, 타임아웃, JSON 깨짐 등 전부
            last_error = e
            logger.warning("LLM 호출 실패 (시도 %d): %s", attempt + 1, type(e).__name__)
    raise LLMError(str(last_error))


@lru_cache(maxsize=4)
def _reusable_anthropic_client(api_key):
    """Claude 연결을 재사용해요 (매번 새로 연결하면 첫 응답이 느려져요)."""
    from anthropic import Anthropic
    return Anthropic(api_key=api_key, max_retries=0)


def anthropic_text(system_prompt: str, user_content: str, *, max_tokens: int = 1500,
                   timeout: float | None = None, model: str | None = None) -> str:
    """Claude 한 번 호출 → 답변 글자. 다른 모듈(RAG·OCR)도 이 함수를 같이 써요."""
    client = _reusable_anthropic_client(settings.llm_api_key)
    system_text = system_prompt + "\n\n설명이나 코드블록 없이 JSON 객체 하나만 출력하세요."
    # 프롬프트 캐싱: 긴 규칙 프롬프트는 매번 똑같아서 한 번 저장해 두면 5분 안의 다음 호출은 입력 요금의 10%만 내요.
    # (그래서 훈련 프롬프트는 매 턴 바뀌는 값을 사용자 메시지 쪽으로 옮겨 두었어요)
    system = ([{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
              if len(system_text) >= 2500 else system_text)
    request = dict(
        model=model or settings.llm_model,
        max_tokens=max(max_tokens, 1024),  # JSON 이 중간에 잘리면 전체가 실패해서 넉넉하게
        system=system,
        messages=[{"role": "user", "content": user_content}],
        temperature=settings.llm_temperature,
        timeout=timeout or settings.llm_timeout_seconds,
    )
    try:
        response = client.messages.create(**request)
    except Exception as e:
        # 모델에 따라 temperature 를 받지 않으면 400 → temperature 없이 한 번 더
        if "temperature" not in str(e):
            raise
        request.pop("temperature")
        response = client.messages.create(**request)
    usage = getattr(response, "usage", None)
    if usage is not None:  # 비용 확인용: 캐시에서 읽은 토큰이 많을수록 싸요
        logger.info("claude tokens model=%s in=%s cache_read=%s cache_write=%s out=%s", request["model"],
                    getattr(usage, "input_tokens", "?"), getattr(usage, "cache_read_input_tokens", 0),
                    getattr(usage, "cache_creation_input_tokens", 0), getattr(usage, "output_tokens", "?"))
    return "".join(block.text for block in response.content if block.type == "text")


@lru_cache(maxsize=4)
def _reusable_openai_client(api_key, base_url):
    import httpx
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=base_url or None, max_retries=0,
                  http_client=httpx.Client(limits=httpx.Limits(max_connections=30,
                      max_keepalive_connections=10, keepalive_expiry=60)))


def _call_provider(system_prompt: str, user_content: str, max_tokens: int, timeout: float, *, reasoning_effort: str | None = None, reuse_connection: bool = False,
                   model: str | None = None) -> str:
    provider = settings.llm_provider

    if provider == "mock":
        return mock_llm.respond(system_prompt, user_content)

    if not settings.llm_api_key or not settings.llm_model:
        raise LLMError(".env 에 LLM_API_KEY 와 LLM_MODEL 을 채워주세요.")

    if provider == "openai":
        from openai import OpenAI  # 필요할 때만 불러옴

        client = _reusable_openai_client(settings.llm_api_key, settings.llm_base_url) if reuse_connection else OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
            timeout=timeout,
            max_retries=0,  # 재시도는 위의 generate_json 에서 직접 관리
        )
        request = dict(
            model=model or settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            timeout=timeout,
            response_format={"type": "json_object"},  # JSON 으로만 답하게 강제
            **({"reasoning_effort": reasoning_effort, "max_completion_tokens": max_tokens} if reasoning_effort else {}),
        )
        try:
            response = client.chat.completions.create(**request)
        except Exception as e:
            # 일부 최신 모델은 temperature 를 바꿀 수 없어서 400 으로 거절해요 → temperature 없이 한 번 더
            if "temperature" not in str(e):
                raise
            request.pop("temperature")
            response = client.chat.completions.create(**request)
        return response.choices[0].message.content or ""

    if provider == "anthropic":
        # reasoning_effort 는 OpenAI 추론 모델용이라 Claude 에는 보내지 않아요
        return anthropic_text(system_prompt, user_content, max_tokens=max_tokens, timeout=timeout, model=model)

    raise LLMError(f"알 수 없는 LLM_PROVIDER: {provider}")


def parse_json(raw_text: str) -> dict:
    """LLM 응답 글자에서 JSON 부분만 꺼내 dict 로 바꾼다.

    LLM 이 가끔 ```json ... ``` 같은 걸 붙여서 답하기 때문에, 첫 { 부터 마지막 } 까지만 잘라서 읽는다.
    """
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("응답에 JSON 이 없습니다.")
    data = json.loads(raw_text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON 이 객체 형태가 아닙니다.")
    return data
