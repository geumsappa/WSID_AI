"""현재 OpenAI 키로 접근 가능한 모델 ID 목록."""
from .openai_client import _client

with _client() as client:
    for model in client.models.list():
        print(model.id)
