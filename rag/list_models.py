"""내 키로 쓸 수 있는 Gemini 모델 이름 보기:  python -m rag.list_models"""
from .gemini import _gemini

for m in _gemini().models.list():
    actions = getattr(m, "supported_actions", None) or []
    if "generateContent" in actions or "embedContent" in actions:
        print(f"{m.name:55s} {','.join(a for a in actions if a in ('generateContent', 'embedContent'))}")
