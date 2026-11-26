"""코퍼스·근거 문단·공식 창구 데이터 일관성 검사 (키 없이 실행)."""
import importlib.util
import json
from pathlib import Path

from rag.guard import has_forbidden

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "corpus" / "seed_chunks.jsonl"
MANUAL = ROOT / "data" / "corpus" / "manual_chunks.jsonl"
REFS = ROOT / "data" / "references" / "references.jsonl"
CHANNELS = ROOT / "data" / "references" / "official_channels.jsonl"


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_seed_matches_build_script(tmp_path, monkeypatch):
    """seed_chunks.jsonl 을 직접 고치면 build_seed.py 를 다시 돌릴 때 사라지므로, 둘이 같아야 한다."""
    spec = importlib.util.spec_from_file_location("build_seed", ROOT / "scripts" / "build_seed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out_dir = tmp_path / "data" / "corpus"
    out_dir.mkdir(parents=True)
    (out_dir / "manual_chunks.jsonl").write_text(MANUAL.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(tmp_path / "scripts" / "build_seed.py"))
    module.main()
    assert (out_dir / "seed_chunks.jsonl").read_text(encoding="utf-8") == SEED.read_text(encoding="utf-8")


def test_references_are_unique_and_clean():
    refs = _jsonl(REFS)
    codes = [r["code"] for r in refs]
    assert len(codes) == len(set(codes))
    for r in refs:
        assert not has_forbidden(r["excerpt"], strict=True), r["code"]
        assert 60 <= len(r["excerpt"]) <= 2000, r["code"]
        assert r.get("source_url") or r.get("mirror_url") or r["authority"].startswith("정리:"), r["code"]


def test_official_channels_schema():
    doc_ids = {c["doc_id"] for c in _jsonl(SEED)}
    rows = _jsonl(CHANNELS)
    assert len({r["channel_code"] for r in rows}) == len(rows)
    for r in rows:
        assert r["url"].startswith("https://"), r["channel_code"]
        assert r["status"] == "확인됨" or r["status"].startswith("미확인"), r["channel_code"]
        assert r["reference_doc_id"] is None or r["reference_doc_id"] in doc_ids, r["channel_code"]
