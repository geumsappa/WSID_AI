"""서버 안에서 직접 돌리는 검색 임베딩 (EMBED_PROVIDER=local). OpenAI 키·외부 호출 없이 동작해요.

모델은 처음 쓸 때 한 번만 메모리에 올리고(서버 시작 때 warm_up), 이후 질문마다 CPU로 벡터를 만들어요.
모델 파일이 없으면 Hugging Face에서 한 번 내려받아요. 배포 이미지에는 미리 넣어 두세요.
"""
from __future__ import annotations

import logging
import threading

from . import config

logger = logging.getLogger(__name__)
_model = None
_lock = threading.Lock()


def prefixes(model_name: str) -> tuple[str, str]:
    """(질문 접두어, 문단 접두어). e5 계열은 접두어가 없으면 에러 없이 검색 품질만 떨어져요."""
    if "e5" in model_name.lower().rsplit("/", 1)[-1]:
        return "query: ", "passage: "
    return "", ""


def model():
    global _model
    with _lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            loaded = SentenceTransformer(config.EMBED_MODEL, device="cpu")
            dim = loaded.get_embedding_dimension()
            if dim != config.EMBED_DIM:
                raise RuntimeError(f"LOCAL_EMBED_DIM({config.EMBED_DIM})이 모델 차원({dim})과 달라요.")
            _model = loaded
    return _model


def warm_up() -> None:
    """서버 시작 때 모델을 미리 올려 첫 질문이 로딩 시간을 기다리지 않게 해요. 실패해도 서버는 떠요."""
    if config.FAKE or config.EMBED_PROVIDER != "local":
        return
    try:
        model()
        logger.info("local embedding ready: %s", config.EMBED_MODEL)
    except Exception as error:  # 검색만 실패하고 다른 API는 계속 동작해야 해요
        logger.error("local embedding unavailable: %s", type(error).__name__)


def _inputs(texts: list[str], task: str) -> list[str]:
    query, passage = prefixes(config.EMBED_MODEL)
    prefix = query if task == "RETRIEVAL_QUERY" else passage
    return [prefix + text for text in texts]


def too_long(texts: list[str]) -> list[int]:
    """모델 입력 상한을 넘어 뒷부분이 잘리는 문단의 위치. 인덱스를 만들 때 검사해요."""
    m = model()
    limit = m.max_seq_length
    return [i for i, text in enumerate(_inputs(texts, "RETRIEVAL_DOCUMENT"))
            if len(m.tokenizer(text)["input_ids"]) > limit]


def encode(texts: list[str], task: str) -> list[list[float]]:
    if task not in ("RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"):
        raise ValueError("invalid retrieval task")
    vectors = model().encode(_inputs(texts, task), normalize_embeddings=True, batch_size=16)
    return vectors.tolist()
