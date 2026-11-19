"""LLM 호출을 한 곳에 모아둔 파일.

다른 코드는 generate_json() 하나만 부르면 됩니다.
LLM 공급자를 바꾸고 싶으면 .env 의 LLM_PROVIDER 만 바꾸면 되고, 다른 파일은 손댈 필요가 없습니다.
"""
import json
import logging

from app.config import settings
from app.llm import mock_llm

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 호출 또는 응답 해석에 실패했을 때 쓰는 에러."""


def generate_json(system_prompt: str, user_content: str, *, max_tokens: int = 800,
                  timeout: float | None = None, attempts: int = 2) -> dict:
    """LLM 에게 묻고, 응답을 JSON(dict)으로 바꿔서 돌려준다.

    attempts 번까지 시도하고, 그래도 안 되면 LLMError 를 던진다.
    (그러면 호출한 쪽에서 기본 문구로 대체)
    max_tokens: 답변 최대 길이 / timeout: 이번 호출만 다른 대기시간을 쓰고 싶을 때
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            raw_text = _call_provider(system_prompt, user_content, max_tokens,
                                      timeout or settings.llm_timeout_seconds)
            return parse_json(raw_text)
        except Exception as e:  # 네트워크 오류, 타임아웃, JSON 깨짐 등 전부
            last_error = e
            logger.warning("LLM 호출 실패 (시도 %d): %s", attempt + 1, type(e).__name__)
    raise LLMError(str(last_error))


def _call_provider(system_prompt: str, user_content: str, max_tokens: int, timeout: float) -> str:
    provider = settings.llm_provider

    if provider == "mock":
        return mock_llm.respond(system_prompt, user_content)

    if not settings.llm_api_key or not settings.llm_model:
        raise LLMError(".env 에 LLM_API_KEY 와 LLM_MODEL 을 채워주세요.")

    if provider == "openai":
        from openai import OpenAI  # 필요할 때만 불러옴

        client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
            timeout=timeout,
            max_retries=0,  # 재시도는 위의 generate_json 에서 직접 관리
        )
        request = dict(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},  # JSON 으로만 답하게 강제
            temperature=0.7,
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
        from anthropic import Anthropic

        client = Anthropic(
            api_key=settings.llm_api_key,
            timeout=timeout,
            max_retries=0,
        )
        response = client.messages.create(
            model=settings.llm_model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            temperature=0.7,
        )
        return "".join(block.text for block in response.content if block.type == "text")

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
