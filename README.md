# WSID_AI

금사빠_어떡하지 AI 저장소. 지금은 **긴급대응 AI-07(피해 대응 근거 설명)의 RAG 핵심 모듈**이 들어 있어요.
서버(FastAPI)는 아직 없습니다. 백엔드 OHC 브랜치의 `docs/ai-07-solution-explain-plan.md` 기준으로
FastAPI는 2차 작업이고, 지금은 그때 그대로 감쌀 수 있는 **검색·설명 함수와 근거 문단 데이터**를 먼저 만듭니다.

## 전체 흐름에서 이 레포가 맡는 부분

```
프론트 → Spring(SOL-03) → AI-06 사실 추출: transferredMoney / contactContinues / sharedInformation / installedApp
                        → Spring 규칙: 긴급이면 대응 카드만 먼저 표시 (AI-07 안 부름)
                        → 근거 문단 고르기        ← rag/retrieve.py  find_references()
                        → AI-07 설명 생성          ← rag/explain.py   explain()
                        → Spring 검증·저장 → 프론트 (연락처·링크는 카드에서만)
```

- 근거 문단을 Spring 카탈로그가 고를지, 이 레포의 검색이 고를지는 아직 팀 합의 전입니다(계획서 11장). 어느 쪽이든 `references.jsonl`이 그 원본 데이터가 됩니다.
- AI-07 요청·응답 형식, 금지 출력 규칙은 Spring 코드(`SolutionExplainRequest`, `SolutionExplanationValidator`)와 똑같이 맞췄어요.

## 처음 설정 (VS Code 터미널)

```bash
git clone https://github.com/geumsappa/WSID_AI.git
cd WSID_AI
python -m venv .venv
# Windows: .venv\Scripts\activate    /   Mac: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # Windows: copy .env.example .env
# .env 를 열어 GEMINI_API_KEY=받은키 입력. .env 는 .gitignore 에 있어서 깃에 안 올라가요.
```

**API 키는 절대 코드나 깃에 넣지 마세요.** 실수로 올렸다면 Google AI Studio에서 키를 삭제하고 새로 받으세요.

## 실행

```bash
# 0) 키 없이 흐름만 확인 (가짜 임베딩·가짜 설명)
#    Mac/Linux: RAG_FAKE=1 python tests/test_rag.py
#    Windows PowerShell: $env:RAG_FAKE="1"; python tests/test_rag.py

# 1) 근거 문단 만들기: 코퍼스 → 연락처·링크 문장 제거 → data/references/references.jsonl
python scripts/build_references.py

# 2) 쓸 수 있는 모델 이름 확인 (GEMINI_CHAT_MODEL 이 안 맞으면 .env 에서 바꾸기)
python -m rag.list_models

# 3) 인덱스 만들기 (Gemini 임베딩 → data/index/). 문단을 바꾸면 다시 실행
python -m rag.index

# 4) 돌려보기
python try_rag.py --money YES --app UNKNOWN
python try_rag.py --info YES --type INVESTMENT_FRAUD
python try_rag.py --money YES --type SECONDHAND_TRADE --debug
```

`try_rag.py`는 Spring이 AI-07에 보낼 요청과 AI가 돌려줄 응답을 그대로 보여줍니다.

## 폴더

| 경로 | 내용 |
| --- | --- |
| `data/corpus/seed_chunks.jsonl` | 공식 자료 31건 요약 청크 38개 (연락처 포함 원본, 카드·검수용) |
| `data/references/references.jsonl` | AI-07에 넣을 근거 문단 (`code`·`title`·`excerpt` + 검색용 태그). 모두 `reviewed: false` |
| `data/index/` | 임베딩 인덱스 (자동 생성, 깃에 안 올림) |
| `rag/` | `config` 설정 · `guard` 연락처/링크 금지 규칙 · `facts` 사실→상황 코드 · `gemini` API 호출 · `index` 인덱스 · `retrieve` 검색 · `explain` 설명 생성·검증 |
| `scripts/` | `hana_fraud_crawler.py` PDF 수집 → `parse_pdfs.py` 청크 → `merge_corpus.py` 병합 → `build_references.py` 근거 문단 |
| `tests/test_rag.py` | 키 없이 도는 테스트 (Spring 금지 규칙과 같은지, 코드 대조, 파이프라인) |

## 근거 문단 규칙 (Spring 계약)

- `code`: `[A-Z0-9_]{1,100}`, 문단 버전마다 고정 (예: `REF_FSS_2026_04_AIVOICE_V1_P1`). 같은 코드에 다른 내용을 덮어쓰지 말고 V2 로 올리기
- `title` 200자, `excerpt` 2,000자 이내, 한 요청에 최대 10개
- **excerpt 에 전화번호·URL 금지.** 연락처·공식 링크는 Spring `content/response-cards` YAML 카드에서만 보여줘요. `build_references.py`는 연락처가 든 문장을 통째로 빼고, 남은 게 너무 짧으면 그 문단을 제외해요.
- `reviewed: true`로 바꾼 문단만 실제로 쓰기 (`python -m rag.index --reviewed-only`)

## 원문 PDF로 바꾸기

지금 코퍼스는 공식 자료를 요약한 것(`source_type: curated_summary`)이에요. 원문으로 바꾸려면:

```bash
python scripts/hana_fraud_crawler.py --since 2024-01-01 --out ./hana_pdfs
python scripts/parse_pdfs.py --pdf-dir ./hana_pdfs/pdf --manifest ./hana_pdfs/manifest.json --out ./rag_out
python scripts/merge_corpus.py --seed data/corpus/seed_chunks.jsonl --parsed ./rag_out/chunks.jsonl --out data/corpus/corpus.jsonl
python scripts/build_references.py --corpus data/corpus/corpus.jsonl
python -m rag.index
```

## 나중에 FastAPI 붙일 때

```python
# routers/solutions.py (2차, 예시)
@router.post("/internal/ai/solutions/explain")
def explain_route(body: ExplainRequest, x_internal_api_key: str = Header(...), x_request_id: str = Header(...)):
    check_key(x_internal_api_key)
    try:
        return {"success": True, "data": explain(body.facts, [r.model_dump() for r in body.verifiedReferences])}
    except ExplainError:
        return {"success": False}
```
Spring 설정: `AI_MODE=http`, `AI_BASE_URL=http://localhost:8000`, `AI_API_KEY`는 양쪽 같은 값.

## 아직 안 된 것

- 실제 Gemini 호출은 이 저장소 작성 환경에서 돌려보지 못했어요(네트워크 제한). 처음 실행할 때 `python -m rag.list_models`로 모델 이름부터 확인하세요.
- 근거 충실성 평가(설명이 문단에 없는 말을 하는지)는 사람이 샘플을 읽어 보는 수준으로 먼저 하고, 기준은 백엔드와 합의해야 합니다.
- 근거 문단을 누가 고를지(Spring 카탈로그 vs AI 검색), 승인·버전 관리 방식은 계획서 11장 합의 항목입니다.
