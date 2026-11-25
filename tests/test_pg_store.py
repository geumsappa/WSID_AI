"""RAG_STORE=pgvector. 실제 PostgreSQL(pgvector)이 있을 때만 돌아요:
RAG_TEST_DB_URL=postgresql://postgres:비밀번호@localhost:5432/wsid pytest tests/test_pg_store.py
테스트마다 임시 스키마를 만들고 끝나면 지워요."""
import asyncio
import json
import os
import uuid

import numpy as np
import pytest

from rag import config, index

DB_URL = os.getenv("RAG_TEST_DB_URL")
REFS = [
    {"code": "REF_A", "version": "1", "title": "송금 후 지급정지", "excerpt": "돈을 보냈다면 금융회사에 지급정지를 요청한다.",
     "fraud_types": ["SMISHING"], "situation_codes": ["money_sent"], "content_kind": "procedure", "reviewed": True},
    {"code": "REF_B", "version": "1", "title": "중고거래 민사", "excerpt": "중고거래 사기는 지급명령이나 소액소송으로 돌려받는다.",
     "fraud_types": ["SECONDHAND_TRADE"], "situation_codes": ["money_sent"], "content_kind": "legal", "reviewed": False},
    {"code": "REF_C", "version": "2", "title": "악성 앱", "excerpt": "링크로 설치한 앱은 지우고 휴대폰을 점검한다.",
     "fraud_types": [], "situation_codes": ["malicious_app"], "content_kind": "procedure", "reviewed": True},
]


@pytest.fixture
def fake_refs(monkeypatch, tmp_path):
    path = tmp_path / "references.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in REFS), encoding="utf-8")
    monkeypatch.setattr(config, "REFERENCES_PATH", path)
    monkeypatch.setattr(config, "FAKE", True)
    monkeypatch.setattr(config, "INDEX_DIR", tmp_path / "index")


def query_vector(text):
    from rag.openai_client import _fake_vec
    q = np.asarray(_fake_vec(text, config.EMBED_DIM), dtype=np.float32)
    return q / (np.linalg.norm(q) + 1e-12)


def test_file_store_nearest_is_sorted_by_similarity(fake_refs, monkeypatch):
    monkeypatch.setattr(config, "RAG_STORE", "file")
    index.build()
    q = query_vector("중고거래 사기 소액소송")
    pairs = index.nearest(q, 10)
    assert [s for s, _ in pairs] == sorted((s for s, _ in pairs), reverse=True)
    assert pairs[0][1]["code"] == "REF_B"


pg = pytest.mark.skipif(not DB_URL, reason="RAG_TEST_DB_URL 이 없으면 DB 테스트를 건너뛰어요")


@pytest.fixture
def pg_store(fake_refs, monkeypatch):
    from rag import pg_store as store
    schema = f"rag_test_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(config, "RAG_STORE", "pgvector")
    monkeypatch.setattr(config, "RAG_DB_URL", DB_URL)
    monkeypatch.setattr(config, "RAG_DB_SCHEMA", schema)
    store.close()
    yield store
    store.close()
    config.RAG_DB_URL = DB_URL  # 테스트가 일부러 잘못된 주소로 바꿨어도 정리는 실제 DB에서
    with store.pool().connection() as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    store.close()


@pg
def test_build_and_load_round_trip(pg_store):
    assert index.build() == 3
    vectors, refs, model = index.load()
    assert [r["code"] for r in refs] == ["REF_A", "REF_B", "REF_C"] and refs == REFS
    assert vectors.shape == (3, config.EMBED_DIM) and model == "FAKE"
    assert index.stale_reason() is None


@pg
def test_db_nearest_matches_file_order(pg_store, monkeypatch):
    index.build()
    q = query_vector("링크 눌러서 앱을 설치했어요")
    from_db = index.nearest(q, 10)
    monkeypatch.setattr(config, "RAG_STORE", "file")
    index.build()
    from_file = index.nearest(q, 10)
    assert [r["code"] for _, r in from_db] == [r["code"] for _, r in from_file]
    assert np.allclose([s for s, _ in from_db], [s for s, _ in from_file], atol=1e-5)
    monkeypatch.setattr(config, "RAG_STORE", "pgvector")
    assert len(index.nearest(q, 2)) == 2  # LIMIT 은 DB에서 걸어요


@pg
def test_rebuild_replaces_rows_and_detects_stale(pg_store, monkeypatch, tmp_path):
    index.build()
    path = config.REFERENCES_PATH
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in REFS[:2]), encoding="utf-8")
    assert "references.jsonl" in index.stale_reason()
    assert index.build() == 2
    assert [r["code"] for r in index.load()[1]] == ["REF_A", "REF_B"]
    assert index.stale_reason() is None


@pg
def test_db_rejects_index_built_with_other_settings(pg_store, monkeypatch):
    index.build()
    monkeypatch.setattr(config, "FAKE", False)  # 가짜 임베딩으로 만든 인덱스를 실제 모델 설정으로 읽으면 거절
    with pytest.raises(SystemExit, match="다시 만드세요"):
        index.load()


@pg
def test_missing_db_index_and_bad_connection_are_clear_errors(pg_store, monkeypatch):
    with pytest.raises(SystemExit, match="DB에 인덱스가 없어요"):
        index.load()
    pg_store.close()
    monkeypatch.setattr(config, "RAG_DB_URL", "postgresql://nobody:x@127.0.0.1:1/none?connect_timeout=1")
    with pytest.raises(SystemExit, match="RAG DB에 연결하지 못했어요"):
        index.nearest(np.zeros(config.EMBED_DIM, dtype=np.float32), 3)
    pg_store.close()
    monkeypatch.setattr(config, "RAG_DB_URL", DB_URL)


@pg
def test_assist_search_reads_from_db(pg_store, monkeypatch):
    from assist.retrieval import ReferenceRetriever
    from assist.upstream import ReferenceIndexUnavailable
    index.build()
    found = asyncio.run(ReferenceRetriever().search("중고거래 사기 소액소송", {"transferredMoney": "YES"},
                                                    fraud_type="SECONDHAND_TRADE"))
    assert found[0]["code"] == "REF_B" and {r["code"] for r in found} == {"REF_A", "REF_B", "REF_C"}
    pg_store.close()
    monkeypatch.setattr(config, "RAG_DB_URL", "postgresql://nobody:x@127.0.0.1:1/none?connect_timeout=1")
    with pytest.raises(ReferenceIndexUnavailable):
        asyncio.run(ReferenceRetriever().search("앱", {}))
