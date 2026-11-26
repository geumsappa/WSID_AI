# RAG 인덱스를 PostgreSQL(pgvector)로 옮기기

근거 문단과 임베딩 벡터를 `data/index` 파일 대신 RDS PostgreSQL의 `rag` 스키마에 저장하고, 검색도 DB에서 코사인 거리(`<=>`) 순으로 해요.
`RAG_STORE=pgvector`일 때만 켜져요. 기본값은 `file`이라서, 아래 준비가 끝나기 전에 코드가 배포돼도 동작은 바뀌지 않아요.

## 구조

| 테이블 | 내용 |
|---|---|
| `rag.reference_chunk` | 문단 1개 = 1행. `code`(PK), `title`, `excerpt`, `fraud_types[]`, `situation_codes[]`, `content_kind`, `reviewed`, 원본 `doc`(jsonb), `embedding vector(1024)` |
| `rag.index_meta` | 인덱스를 만든 임베딩 공급자·모델·차원. 현재 설정과 다르면 검색을 거절해요(503) |

- 서버가 시작할 때 `python -m rag.index --if-stale`가 설정이나 문단이 바뀌었는지 보고, 바뀌었으면 DB 인덱스를 다시 만들어요. 테이블은 한 트랜잭션 안에서 통째로 바꿔요.
- 문단이 수백 개 수준이라 근사 인덱스(HNSW) 없이 정확 검색을 해요. 수천 개를 넘으면 HNSW 인덱스와 `assist/retrieval.py`의 `NEAREST_POOL`을 다시 정해요.
- 상황·유형 가산점과 중고거래 제외 규칙은 지금처럼 파이썬에서 적용해요. 평가 질문 102개로 비교했을 때 파일 방식과 상위 10개 순위가 모두 같았어요. 검색 1회는 임베딩 포함 약 130ms예요.

## 운영 준비 (한 번만)

1. **RDS에 확장과 전용 계정 만들기** (마스터 계정으로 `wsid` DB에 접속)
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   CREATE ROLE wsid_ai_rag LOGIN PASSWORD '<새 비밀번호>';
   CREATE SCHEMA IF NOT EXISTS rag AUTHORIZATION wsid_ai_rag;
   ```
   AI는 `rag` 스키마만 써요. 서비스 테이블(public 등)에는 권한을 주지 않아요.
2. **보안그룹**: RDS 보안그룹의 5432 인바운드에 `wsid-ai` 인스턴스의 보안그룹을 추가해요.
3. **AI_ENV 시크릿에 추가**
   ```
   RAG_STORE=pgvector
   RAG_DB_URL=postgresql://wsid_ai_rag:<비밀번호>@<RDS 엔드포인트>:5432/wsid?sslmode=require
   ```
4. develop에 push하면 배포 → 컨테이너가 시작할 때 DB 인덱스를 만들어요. 로그에서 `인덱스 완료: 문단 82개 → DB rag.reference_chunk`를 확인해요.

## 되돌리기

AI_ENV에서 `RAG_STORE=file`로 바꾸고 다시 배포하면 파일 인덱스로 돌아가요. DB 테이블은 그대로 둬도 돼요.

## 로컬에서 시험하기

```bash
docker run -d --name wsid-pgvector -e POSTGRES_PASSWORD=test -e POSTGRES_DB=wsid -p 127.0.0.1:5433:5432 pgvector/pgvector:pg17
export RAG_STORE=pgvector RAG_DB_URL=postgresql://postgres:test@127.0.0.1:5433/wsid
python -m rag.index
RAG_TEST_DB_URL=$RAG_DB_URL pytest tests/test_pg_store.py
```
