"""근거 문단을 임베딩해서 로컬 파일 인덱스로 저장·로드.
문단이 수백 개 수준이라 벡터DB 없이 numpy 로 충분합니다. 수천 개를 넘거나 서버를 띄울 때 Chroma 등으로 바꾸면 돼요.

python -m rag.index            # 인덱스 만들기 (문단을 바꿀 때마다 다시 실행)
python -m rag.index --reviewed-only   # 검토 완료(reviewed=true) 문단만
python -m rag.index --if-stale        # 설정·문단이 바뀌었을 때만 다시 만들기 (서버 시작 때마다 실행)
RAG_STORE=pgvector 면 파일 대신 PostgreSQL(pgvector) 테이블에 저장하고, 검색도 DB에서 거리 순으로 해요 (rag/pg_store.py).
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from . import config
from .openai_client import embed


def _embed_text(r: dict) -> str:
    return f"[{r['title']}]\n{r['excerpt']}"


def _references(reviewed_only: bool) -> list[dict]:
    refs = [json.loads(l) for l in config.REFERENCES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in refs if r.get("reviewed")] if reviewed_only else refs


def stale_reason(reviewed_only: bool = False) -> str | None:
    """인덱스를 다시 만들어야 하는 이유. 최신이면 None.
    설정(공급자·모델·차원)이 다르거나 references.jsonl 문단이 추가·수정·삭제됐으면 다시 만들어야 해요."""
    try:
        _, indexed, _ = load()
    except SystemExit as error:
        return str(error)
    if indexed != _references(reviewed_only):
        return "근거 문단(references.jsonl)이 인덱스를 만든 뒤 바뀌었어요."
    return None


def build(reviewed_only: bool = False) -> int:
    refs = _references(reviewed_only)
    if not refs:
        raise SystemExit("인덱스에 넣을 문단이 없어요. scripts/build_references.py 를 먼저 실행하세요.")
    texts = [_embed_text(r) for r in refs]
    if config.embed_provider() == "local":
        from .local_embed import too_long
        if over := too_long(texts):
            # 상한을 넘으면 뒷부분이 조용히 잘려 그 내용으로는 검색되지 않아요
            raise SystemExit("임베딩 입력 상한을 넘는 문단이 있어요. 문단을 나누세요: "
                             + ", ".join(refs[i]["code"] for i in over))
    vecs = np.array(embed(texts, task="RETRIEVAL_DOCUMENT"), dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
    if config.RAG_STORE == "pgvector":
        from . import pg_store
        pg_store.write(refs, vecs, _want_model(), config.embed_provider())
        return len(refs)
    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(config.INDEX_DIR / "vectors.npy", vecs)
    meta = {"embed_model": _want_model(), "provider": config.embed_provider(), "dim": int(vecs.shape[1]), "refs": refs}
    (config.INDEX_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return len(refs)


def _want_model() -> str:
    return "FAKE" if config.FAKE else config.EMBED_MODEL


def load() -> tuple[np.ndarray, list[dict], str]:
    if config.RAG_STORE == "pgvector":
        from . import pg_store
        return pg_store.load(_want_model(), config.embed_provider())
    p = config.INDEX_DIR / "meta.json"
    if not p.exists():
        raise SystemExit("인덱스가 없어요. `python -m rag.index` 를 먼저 실행하세요.")
    meta = json.loads(p.read_text(encoding="utf-8"))
    want = "FAKE" if config.FAKE else config.EMBED_MODEL
    if meta["embed_model"] != want or meta.get("provider") != config.embed_provider() or meta.get("dim") != config.EMBED_DIM:
        raise SystemExit(f"인덱스 임베딩({meta['embed_model']})과 현재 설정({want})이 달라요. 인덱스를 다시 만드세요.")
    vectors = np.load(config.INDEX_DIR / "vectors.npy")
    if vectors.ndim != 2 or vectors.shape != (len(meta["refs"]), config.EMBED_DIM):
        raise SystemExit("인덱스 벡터 크기가 설정 또는 근거 목록과 달라요. 인덱스를 다시 만드세요.")
    return vectors, meta["refs"], meta["embed_model"]


def nearest(query: np.ndarray, limit: int) -> list[tuple[float, dict]]:
    """정규화된 질문 벡터와 가까운 문단 limit개 (유사도, 문단). pgvector면 DB가 거리 순으로 골라요."""
    if config.RAG_STORE == "pgvector":
        from . import pg_store
        return pg_store.nearest(query, limit, _want_model(), config.embed_provider())
    vectors, refs, _ = load()
    scores = vectors @ query
    order = np.argsort(-scores, kind="stable")[:limit]
    return [(float(scores[i]), refs[i]) for i in order]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewed-only", action="store_true")
    ap.add_argument("--if-stale", action="store_true", help="설정·문단이 그대로면 다시 만들지 않아요 (서버 시작용)")
    a = ap.parse_args()
    if a.if_stale:
        reason = stale_reason(a.reviewed_only)
        if reason is None:
            print(f"인덱스가 최신이라 건너뛰어요 ({config.embed_provider()} {config.EMBED_MODEL})")
            raise SystemExit(0)
        print(f"인덱스를 다시 만들어요: {reason}")
    n = build(a.reviewed_only)
    where = f"DB {config.RAG_DB_SCHEMA}.reference_chunk" if config.RAG_STORE == "pgvector" else config.INDEX_DIR
    print(f"인덱스 완료: 문단 {n}개 → {where} ({'가짜 임베딩' if config.FAKE else config.EMBED_MODEL})")
