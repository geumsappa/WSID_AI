#!/usr/bin/env python3
"""Gemini 연결 진단: 키·임베딩·답변 모델이 각각 응답하는지, 몇 초 걸리는지 확인.
  python check_gemini.py
"""
import time

from rag import config
from rag.gemini import _gemini, _status

print(f"키: {'있음' if config.GEMINI_API_KEY else '없음 (.env 확인)'} / 대기 상한 {config.TIMEOUT_SEC}초")
print(f"기본 모델: {config.CHAT_MODEL!r} / 예비: {config.FALLBACK_MODELS}\n")
client = _gemini()


def run(label, fn):
    t0 = time.time()
    try:
        fn()
        print(f"  ✓ {label}: {time.time() - t0:.1f}초")
    except Exception as e:  # noqa: BLE001
        msg = str(e).split("\n")[0][:150]
        print(f"  ✗ {label}: {time.time() - t0:.1f}초, {_status(e) or type(e).__name__} {msg}")


run(f"임베딩 {config.EMBED_MODEL}", lambda: client.models.embed_content(model=config.EMBED_MODEL, contents=["테스트"]))
for m in [config.CHAT_MODEL, *config.FALLBACK_MODELS]:
    run(f"답변 {m}", lambda m=m: client.models.generate_content(model=m, contents="'확인'이라고만 답해"))
