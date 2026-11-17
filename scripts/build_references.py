#!/usr/bin/env python3
"""코퍼스 청크 → AI-07 근거 문단(references.jsonl) 변환.

Spring AI-07 계약은 근거를 {code, title, excerpt} 로 받고, 설명에 연락처·링크가 나오면 안 됩니다.
그래서 청크에서 연락처·링크가 든 '문장 전체'를 빼고 설명용 문단(excerpt)을 만듭니다.
연락처·링크·출처 URL 은 Spring 대응 카드(response-cards YAML)에서만 보여줍니다.

모든 문단은 reviewed=false 로 만들어요. 팀이 읽고 확인한 문단만 true 로 바꾸세요.

python scripts/build_references.py --corpus data/corpus/seed_chunks.jsonl
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from rag.config import MAX_EXCERPT, MAX_TITLE, REFERENCES_PATH  # noqa: E402
from rag.guard import has_forbidden, strip_contact_sentences  # noqa: E402

MIN_EXCERPT = 60


def to_code(chunk_id: str, version: int) -> str:
    doc, _, n = chunk_id.partition("#")
    base = re.sub(r"[^A-Z0-9]+", "_", doc.upper()).strip("_")
    return f"REF_{base}_V{version}_P{int(n or 1)}"[:100]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", nargs="+", default=[str(ROOT / "data/corpus/seed_chunks.jsonl")])
    ap.add_argument("--out", default=str(REFERENCES_PATH))
    ap.add_argument("--version", type=int, default=1)
    a = ap.parse_args()

    refs, skipped = [], []
    for path in a.corpus:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            excerpt, dropped = strip_contact_sentences(c["text"])
            if len(excerpt) < MIN_EXCERPT or has_forbidden(excerpt, strict=True):
                skipped.append({"chunk_id": c["chunk_id"], "reason": "연락처 위주 문단(카드로 보낼 내용)"})
                continue
            title = (f"{c['title']} - {c['section']}" if c.get("section") else c["title"])[:MAX_TITLE]
            refs.append({
                # ---- AI-07 요청에 그대로 들어가는 3개 필드
                "code": to_code(c["chunk_id"], a.version),
                "title": title,
                "excerpt": excerpt[:MAX_EXCERPT],
                # ---- 검색·관리용 (AI-07 요청에는 안 보냄)
                "chunk_id": c["chunk_id"],
                "fraud_types": c.get("fraud_types", []),
                "situation_codes": c.get("situation_codes", []),
                "content_kind": c.get("content_kind"),
                "authority": c.get("authority"),
                "published_at": c.get("published_at"),
                "source_url": c.get("source_url"),
                "mirror_url": c.get("mirror_url"),
                "source_type": c.get("source_type"),
                "verified_at": c.get("verified_at"),
                "dropped_contact_sentences": dropped,
                "reviewed": False,
            })
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in refs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"근거 문단 {len(refs)}개 → {out}")
    print(f"연락처 문장을 뺀 문단 {sum(1 for r in refs if r['dropped_contact_sentences'])}개 (검토 때 의미가 바뀌지 않았는지 확인)")
    for s in skipped:
        print(f"  제외: {s['chunk_id']} - {s['reason']}")


if __name__ == "__main__":
    main()
