"""질문·intent·facts로 후보 검색. 승인은 Spring이 수행하며 후보는 승인 표시가 아닙니다."""
from __future__ import annotations

import numpy as np

from rag import config
from rag.facts import situations_for
from rag.index import load

from .contracts import CandidateReference, VerifiedReference
from .upstream import ReferenceIndexUnavailable, call


class ReferenceRetriever:
    async def search(self, query: str, facts: dict, k: int = 10) -> list[dict]:
        try:
            vectors, references, _ = load()
        except SystemExit:
            # CLI용 load()가 API 서버 프로세스를 종료하지 않도록 변환한다.
            raise ReferenceIndexUnavailable("reference index unavailable") from None
        if not references:
            return []
        if config.FAKE:
            from rag.openai_client import _fake_vec
            vector = _fake_vec(query, config.EMBED_DIM)
        else:
            from rag.openai_client import async_client
            async with async_client() as client:
                response = await call(lambda: client.embeddings.create(
                    model=config.EMBED_MODEL, input=[query], dimensions=config.EMBED_DIM,
                ), "embedding")
            vector = response.data[0].embedding
        q = np.asarray(vector, dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-12
        scores = vectors @ q
        wanted = set(situations_for(facts))
        candidates = []
        codes = set()
        for score, ref in zip(scores, references, strict=True):
            # 버전 누락을 추측해서 채우지 않는다. 빌더와 문서의 명시적 버전 체계를 사용.
            identity = CandidateReference.model_validate(ref)
            content = VerifiedReference.model_validate(ref)
            if identity.code in codes:
                raise ValueError("duplicate indexed reference code")
            codes.add(identity.code)
            bonus = 0.02 * len(wanted & set(ref.get("situation_codes", [])))
            candidates.append((float(score) + bonus, {**content.model_dump(), **identity.model_dump()}))
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        # YES가 없어도 예방·일반 질문을 검색한다. 적합성과 적용 조건은 PLAN 모델이 평가.
        return [ref for _, ref in candidates[:min(k, 10)]]
