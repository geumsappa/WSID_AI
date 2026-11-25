"""EMBED_PROVIDER=local: 모델 파일 없이 가짜 모델로 분기·접두어·인덱스 검사를 확인해요."""
import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest

from rag import config, index, local_embed, openai_client


class FakeModel:
    """sentence-transformers 모델 대역: 글자 수로 벡터를 만들고 받은 입력을 기록해요."""
    max_seq_length = 40

    def __init__(self, dim=3):
        self.dim = dim
        self.inputs = []
        self.tokenizer = lambda text: {"input_ids": list(text)}

    def get_embedding_dimension(self):
        return self.dim

    def encode(self, texts, normalize_embeddings, batch_size):
        assert normalize_embeddings
        self.inputs.extend(texts)
        return np.array([[len(t), 1.0, 0.0] for t in texts], dtype=np.float32)


@pytest.fixture
def local(monkeypatch, tmp_path):
    model = FakeModel()
    monkeypatch.setattr(config, "FAKE", False)
    monkeypatch.setattr(config, "EMBED_PROVIDER", "local")
    monkeypatch.setattr(config, "EMBED_MODEL", "intfloat/multilingual-e5-large")
    monkeypatch.setattr(config, "EMBED_DIM", 3)
    monkeypatch.setattr(config, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(local_embed, "_model", model)
    return model


def write_refs(monkeypatch, tmp_path, refs):
    path = tmp_path / "references.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in refs), encoding="utf-8")
    monkeypatch.setattr(config, "REFERENCES_PATH", path)


REF = {"code": "REF_A", "version": "1", "title": "제목", "excerpt": "짧은 문단"}


def test_e5_needs_query_and_passage_prefixes():
    assert local_embed.prefixes("intfloat/multilingual-e5-large") == ("query: ", "passage: ")
    assert local_embed.prefixes("nlpai-lab/KURE-v1") == ("", "")
    assert local_embed.prefixes("BAAI/bge-m3") == ("", "")


def test_embed_routes_to_local_model_with_prefix(local):
    openai_client.embed(["질문"], "RETRIEVAL_QUERY")
    openai_client.embed(["문단"], "RETRIEVAL_DOCUMENT")
    assert local.inputs == ["query: 질문", "passage: 문단"]


def test_local_needs_no_openai_key_even_with_claude(local, monkeypatch):
    monkeypatch.setattr(openai_client, "settings", SimpleNamespace(llm_provider="anthropic", openai_api_key=""))
    assert openai_client.has_embeddings()


def test_index_records_local_provider_and_rejects_other_provider(local, monkeypatch, tmp_path):
    write_refs(monkeypatch, tmp_path, [REF])
    assert index.build() == 1
    meta = json.loads((config.INDEX_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["provider"] == "local" and meta["embed_model"] == config.EMBED_MODEL and meta["dim"] == 3
    index.load()
    # OpenAI로 만든 인덱스를 local 설정으로 읽으면 다시 만들라고 거절해요
    monkeypatch.setattr(config, "EMBED_PROVIDER", "openai")
    with pytest.raises(SystemExit):
        index.load()


def test_stale_index_detected_when_settings_or_paragraphs_change(local, monkeypatch, tmp_path):
    # 서버는 볼륨에 인덱스를 남겨 두므로, 문단이나 설정이 바뀌면 다시 만들어야 한다는 걸 알아내야 해요
    assert index.stale_reason() is not None  # 인덱스 없음
    write_refs(monkeypatch, tmp_path, [REF])
    index.build()
    assert index.stale_reason() is None
    write_refs(monkeypatch, tmp_path, [REF, {**REF, "code": "REF_NEW"}])
    assert "references.jsonl" in index.stale_reason()
    index.build()
    assert index.stale_reason() is None
    write_refs(monkeypatch, tmp_path, [REF, {**REF, "code": "REF_NEW", "excerpt": "고친 문단"}])
    assert index.stale_reason() is not None
    index.build()
    monkeypatch.setattr(config, "EMBED_MODEL", "nlpai-lab/KURE-v1")
    assert index.stale_reason() is not None


def test_index_rejects_paragraph_longer_than_model_limit(local, monkeypatch, tmp_path):
    write_refs(monkeypatch, tmp_path, [REF, {**REF, "code": "REF_LONG", "excerpt": "가" * 40}])
    with pytest.raises(SystemExit, match="REF_LONG"):
        index.build()
    assert not (config.INDEX_DIR / "meta.json").exists()


def test_assist_search_uses_local_query_vector(local, monkeypatch, tmp_path):
    from assist.retrieval import ReferenceRetriever

    write_refs(monkeypatch, tmp_path, [REF, {**REF, "code": "REF_B", "excerpt": "조금 더 긴 문단입니다"}])
    index.build()
    local.inputs.clear()
    found = asyncio.run(ReferenceRetriever().search("앱 설치", {}))
    assert local.inputs == ["query: 앱 설치"]
    assert {r["code"] for r in found} == {"REF_A", "REF_B"}


def test_local_model_failure_becomes_index_unavailable(local, monkeypatch, tmp_path):
    from assist.retrieval import ReferenceRetriever
    from assist.upstream import ReferenceIndexUnavailable

    write_refs(monkeypatch, tmp_path, [REF])
    index.build()

    def broken(texts, task):
        raise OSError("model file missing")

    monkeypatch.setattr(local_embed, "encode", broken)
    with pytest.raises(ReferenceIndexUnavailable):
        asyncio.run(ReferenceRetriever().search("앱 설치", {}))


def test_warm_up_rejects_wrong_dimension_without_crashing(monkeypatch):
    import sentence_transformers

    monkeypatch.setattr(config, "FAKE", False)
    monkeypatch.setattr(config, "EMBED_PROVIDER", "local")
    monkeypatch.setattr(config, "EMBED_DIM", 1024)
    monkeypatch.setattr(local_embed, "_model", None)
    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", lambda name, device: FakeModel(dim=3))
    local_embed.warm_up()  # 서버는 떠야 해요
    assert local_embed._model is None
    with pytest.raises(RuntimeError, match="LOCAL_EMBED_DIM"):
        local_embed.model()
