"""질문·intent·facts로 후보 검색. 승인은 Spring이 수행하며 후보는 승인 표시가 아닙니다."""
from __future__ import annotations

import numpy as np

from rag import config
from rag.facts import situations_for
from rag.index import load
from rag.retrieve import NON_TELECOM_TYPES, REFUND_ONLY_PREFIXES

from .contracts import CandidateReference, VerifiedReference
from .upstream import ReferenceIndexUnavailable, call


class ReferenceRetriever:
    @staticmethod
    def _load():
        try:
            return load()
        except SystemExit:
            # CLI용 load()가 API 서버 프로세스를 종료하지 않도록 변환한다.
            raise ReferenceIndexUnavailable("reference index unavailable") from None

    def by_codes(self, codes: list[str]) -> list[dict]:
        """질문 주제로 꼭 넣을 근거를 code로 꺼내요 (검색과 같은 모양: code·version·title·excerpt)."""
        if not codes:
            return []
        _, references, _ = self._load()
        by_code = {ref["code"]: ref for ref in references}
        out = []
        for code in codes:
            if code in by_code:
                ref = by_code[code]
                out.append({**VerifiedReference.model_validate(ref).model_dump(),
                            **CandidateReference.model_validate(ref).model_dump()})
        return out

    async def search(self, query: str, facts: dict, k: int = 10, fraud_type: str | None = None) -> list[dict]:
        vectors, references, _ = self._load()
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
            # v1.1 fraudType: 중고거래 등 환급법 대상이 아닌 유형이면 지급정지·환급 전용 문단은 빼요
            if fraud_type in NON_TELECOM_TYPES and ref["code"].startswith(REFUND_ONLY_PREFIXES):
                continue
            identity = CandidateReference.model_validate(ref)
            content = VerifiedReference.model_validate(ref)
            if identity.code in codes:
                raise ValueError("duplicate indexed reference code")
            codes.add(identity.code)
            bonus = 0.02 * len(wanted & set(ref.get("situation_codes", [])))
            if fraud_type and fraud_type in ref.get("fraud_types", []):
                bonus += 0.04  # 유형이 맞는 문단을 조금 올려요 (묻는 내용이 우선이라 작게)
            candidates.append((float(score) + bonus, {**content.model_dump(), **identity.model_dump()}))
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        # YES가 없어도 예방·일반 질문을 검색한다. 적합성과 적용 조건은 PLAN 모델이 평가.
        return [ref for _, ref in candidates[:min(k, 10)]]
