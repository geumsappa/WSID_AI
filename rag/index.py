"""근거 문단을 임베딩해서 로컬 파일 인덱스로 저장·로드.
문단이 수백 개 수준이라 벡터DB 없이 numpy 로 충분합니다. 수천 개를 넘거나 서버를 띄울 때 Chroma 등으로 바꾸면 돼요.

python -m rag.index            # 인덱스 만들기 (문단을 바꿀 때마다 다시 실행)
python -m rag.index --reviewed-only   # 검토 완료(reviewed=true) 문단만
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from . import config
from .openai_client import embed


def _embed_text(r: dict) -> str:
    return f"[{r['title']}]\n{r['excerpt']}"


def build(reviewed_only: bool = False) -> int:
    refs = [json.loads(l) for l in config.REFERENCES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    if reviewed_only:
        refs = [r for r in refs if r.get("reviewed")]
    if not refs:
        raise SystemExit("인덱스에 넣을 문단이 없어요. scripts/build_references.py 를 먼저 실행하세요.")
    vecs = np.array(embed([_embed_text(r) for r in refs], task="RETRIEVAL_DOCUMENT"), dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(config.INDEX_DIR / "vectors.npy", vecs)
    meta = {"embed_model": "FAKE" if config.FAKE else config.EMBED_MODEL, "provider": "fake" if config.FAKE else "openai", "dim": int(vecs.shape[1]), "refs": refs}
    (config.INDEX_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return len(refs)


def load() -> tuple[np.ndarray, list[dict], str]:
    p = config.INDEX_DIR / "meta.json"
    if not p.exists():
        raise SystemExit("인덱스가 없어요. `python -m rag.index` 를 먼저 실행하세요.")
    meta = json.loads(p.read_text(encoding="utf-8"))
    want = "FAKE" if config.FAKE else config.EMBED_MODEL
    if meta["embed_model"] != want or meta.get("provider") != ("fake" if config.FAKE else "openai") or meta.get("dim") != config.EMBED_DIM:
        raise SystemExit(f"인덱스 임베딩({meta['embed_model']})과 현재 설정({want})이 달라요. 인덱스를 다시 만드세요.")
    vectors = np.load(config.INDEX_DIR / "vectors.npy")
    if vectors.ndim != 2 or vectors.shape != (len(meta["refs"]), config.EMBED_DIM):
        raise SystemExit("인덱스 벡터 크기가 설정 또는 근거 목록과 달라요. 인덱스를 다시 만드세요.")
    return vectors, meta["refs"], meta["embed_model"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewed-only", action="store_true")
    a = ap.parse_args()
    n = build(a.reviewed_only)
    print(f"인덱스 완료: 문단 {n}개 → {config.INDEX_DIR} ({'가짜 임베딩' if config.FAKE else config.EMBED_MODEL})")
