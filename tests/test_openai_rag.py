import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest

from rag import config, index, openai_client
from assist.model import OpenAIAssistModel
from assist.contracts import Assessment


def test_openai_generation_and_embedding_use_config(monkeypatch):
    calls = []
    class Client:
        def __init__(self, timeout=None):
            calls.append({'timeout': timeout})
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.complete))
            self.embeddings = SimpleNamespace(create=self.embed)
        def complete(self, **kw):
            calls.append(kw)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])
        def embed(self, **kw):
            calls.append(kw)
            return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.5, 0.6])])
        def close(self):
            pass
    monkeypatch.setattr(config, 'FAKE', False)
    monkeypatch.setattr(config, 'EMBED_DIM', 2)
    monkeypatch.setattr(openai_client, '_client', Client)
    monkeypatch.setattr(openai_client, 'settings', SimpleNamespace(llm_model='example-model'))
    assert openai_client.generate_json('system', 'input', {}) == {'ok': True}
    assert openai_client.embed(['test'], 'RETRIEVAL_QUERY') == [[0.5, 0.6]]
    assert calls[0]['timeout'] is None and calls[1]['model'] == 'example-model'
    assert calls[3]['model'] == config.EMBED_MODEL and calls[3]['dimensions'] == 2
    calls.clear()
    openai_client.generate_json('system', 'input', {}, model='fast-model', timeout=5)
    assert calls[0]['timeout'] == 5 and calls[1]['model'] == 'fast-model'


def test_index_rejects_foreign_provider_or_dimensions(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'INDEX_DIR', tmp_path)
    monkeypatch.setattr(config, 'FAKE', False)
    (tmp_path / 'meta.json').write_text(json.dumps({'provider': 'google', 'embed_model': config.EMBED_MODEL,
                                                    'dim': config.EMBED_DIM, 'refs': []}))
    np.save(tmp_path / 'vectors.npy', np.zeros((1, config.EMBED_DIM)))
    with pytest.raises(SystemExit):
        index.load()
    (tmp_path / 'meta.json').write_text(json.dumps({'provider': 'openai', 'embed_model': config.EMBED_MODEL,
                                                    'dim': config.EMBED_DIM - 1, 'refs': []}))
    with pytest.raises(SystemExit):
        index.load()


def test_assist_calls_openai_and_validates_response(monkeypatch):
    from app import config as app_config
    calls = []
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        @property
        def chat(self):
            return SimpleNamespace(completions=SimpleNamespace(create=self.complete))
        async def complete(self, **kwargs):
            calls.append(kwargs)
            text = '{"decision":"SEARCH","questionIntent":"GENERAL","factKey":null}'
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
    monkeypatch.setattr(app_config, 'settings', SimpleNamespace(llm_model='example-model'))
    monkeypatch.setattr(openai_client, 'async_client', Client)
    result = asyncio.run(OpenAIAssistModel()._generate('system', {}, Assessment))
    assert result.decision == 'SEARCH'
    assert calls[0]['model'] == 'example-model'
