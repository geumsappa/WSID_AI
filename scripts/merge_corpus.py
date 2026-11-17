#!/usr/bin/env python3
"""seed_chunks.jsonl(요약본)과 parse_pdfs.py 결과(원문)를 합쳐 최종 corpus.jsonl을 만든다.
같은 PDF(mirror_url이 같음)의 원문 청크가 있으면 요약본 청크는 뺀다.

python merge_corpus.py --seed seed_chunks.jsonl --parsed ./rag_out/chunks.jsonl --out corpus.jsonl
"""
import argparse, json
from pathlib import Path


def load(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


ap = argparse.ArgumentParser()
ap.add_argument("--seed", default="seed_chunks.jsonl")
ap.add_argument("--parsed", nargs="*", default=[])
ap.add_argument("--out", default="corpus.jsonl")
a = ap.parse_args()

parsed = [c for p in a.parsed for c in load(p)]
covered = {c["mirror_url"] for c in parsed if c.get("mirror_url")} | {c["source_url"] for c in parsed if c.get("source_url")}
seed = [c for c in load(a.seed) if not ({c.get("mirror_url"), c.get("source_url")} & covered)]
rows = parsed + seed
with open(a.out, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"원문 {len(parsed)} + 요약 {len(seed)} (원문과 겹친 요약 {len(load(a.seed)) - len(seed)}개 제외) → {a.out}")
