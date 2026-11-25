"""RAG_STORE=pgvector: 근거 문단과 벡터를 PostgreSQL(pgvector)에 저장하고 DB에서 거리 순으로 검색해요.

테이블 (스키마 이름은 RAG_DB_SCHEMA, 기본 rag)
- reference_chunk: 문단 하나가 한 행. 검색 필터용 열(fraud_types·situation_codes·reviewed)과 원본(doc), embedding.
- index_meta: 인덱스를 만든 임베딩 공급자·모델·차원. 읽을 때 현재 설정과 다르면 거절해요.
인덱스를 다시 만들 때는 한 트랜잭션에서 테이블을 통째로 바꿔요. 검색 중인 요청은 이전 인덱스나 새 인덱스 중 하나만 봐요.
문단이 수백 개 수준이라 근사 인덱스(HNSW) 없이 전체 거리 계산(정확 검색)을 써요.
"""
from __future__ import annotations

import json
import threading

import numpy as np

from . import config

_pool = None
_pool_lock = threading.Lock()


def _sql():
    from psycopg import sql
    return sql


def _schema():
    return _sql().Identifier(config.RAG_DB_SCHEMA)


def pool():
    """프로세스당 하나. 첫 사용 때 만들어요 (서버 시작은 DB가 없어도 실패하지 않게)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                if not config.RAG_DB_URL:
                    raise RuntimeError("RAG_STORE=pgvector면 RAG_DB_URL을 설정해 주세요.")
                from pgvector.psycopg import register_vector
                from psycopg_pool import ConnectionPool

                _pool = ConnectionPool(config.RAG_DB_URL, min_size=0, max_size=4, timeout=5,
                                       configure=register_vector, open=True,
                                       kwargs={"connect_timeout": 5, "application_name": "wsid-ai-rag"})
    return _pool


def _ensure_extension(conn) -> None:
    if conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone():
        return
    try:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except Exception as error:
        raise SystemExit("DB에 pgvector 확장이 없어요. DB 관리자 계정으로 `CREATE EXTENSION vector;`를 먼저 실행해 주세요. "
                         f"({type(error).__name__})") from None


def write(refs: list[dict], vectors: np.ndarray, embed_model: str, provider: str) -> None:
    sql = _sql()
    dim = int(vectors.shape[1])
    with pool().connection() as conn:
        _ensure_extension(conn)
        conn.commit()
        from pgvector.psycopg import register_vector
        register_vector(conn)
        with conn.transaction():
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(_schema()))
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {}.reference_chunk").format(_schema()))
            conn.execute(sql.SQL("""
                CREATE TABLE {}.reference_chunk (
                    position        integer NOT NULL UNIQUE,
                    code            text PRIMARY KEY,
                    version         text NOT NULL,
                    title           text NOT NULL,
                    excerpt         text NOT NULL,
                    fraud_types     text[] NOT NULL,
                    situation_codes text[] NOT NULL,
                    content_kind    text,
                    reviewed        boolean NOT NULL,
                    doc             jsonb NOT NULL,
                    embedding       vector({}) NOT NULL
                )""").format(_schema(), sql.Literal(dim)))
            conn.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.index_meta (
                    id          smallint PRIMARY KEY CHECK (id = 1),
                    embed_model text NOT NULL,
                    provider    text NOT NULL,
                    dim         integer NOT NULL,
                    built_at    timestamptz NOT NULL DEFAULT now()
                )""").format(_schema()))
            with conn.cursor() as cur:
                cur.executemany(sql.SQL("""
                    INSERT INTO {}.reference_chunk (position, code, version, title, excerpt, fraud_types,
                        situation_codes, content_kind, reviewed, doc, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""").format(_schema()),
                    [(i, r["code"], str(r["version"]), r["title"], r["excerpt"], list(r.get("fraud_types") or []),
                      list(r.get("situation_codes") or []), r.get("content_kind"), bool(r.get("reviewed")),
                      json.dumps(r, ensure_ascii=False), vectors[i]) for i, r in enumerate(refs)])
            conn.execute(sql.SQL("""
                INSERT INTO {}.index_meta (id, embed_model, provider, dim) VALUES (1, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET embed_model = EXCLUDED.embed_model, provider = EXCLUDED.provider,
                    dim = EXCLUDED.dim, built_at = now()""").format(_schema()), (embed_model, provider, dim))


def _check_meta(conn, want_model: str, want_provider: str) -> None:
    sql = _sql()
    row = conn.execute(sql.SQL("SELECT to_regclass(%s)"), (f"{config.RAG_DB_SCHEMA}.index_meta",)).fetchone()
    meta = None
    if row and row[0]:
        meta = conn.execute(sql.SQL("SELECT embed_model, provider, dim FROM {}.index_meta WHERE id = 1")
                            .format(_schema())).fetchone()
    if meta is None:
        raise SystemExit("DB에 인덱스가 없어요. `python -m rag.index` 를 먼저 실행하세요.")
    if meta[0] != want_model or meta[1] != want_provider or meta[2] != config.EMBED_DIM:
        raise SystemExit(f"인덱스 임베딩({meta[0]})과 현재 설정({want_model})이 달라요. 인덱스를 다시 만드세요.")


def _connect_error(error: Exception) -> SystemExit:
    return SystemExit(f"RAG DB에 연결하지 못했어요 ({type(error).__name__}). RAG_DB_URL과 네트워크를 확인하세요.")


def load(want_model: str, want_provider: str) -> tuple[np.ndarray, list[dict], str]:
    sql = _sql()
    try:
        with pool().connection() as conn:
            _check_meta(conn, want_model, want_provider)
            rows = conn.execute(sql.SQL("SELECT doc, embedding FROM {}.reference_chunk ORDER BY position")
                                .format(_schema())).fetchall()
    except SystemExit:
        raise
    except Exception as error:
        raise _connect_error(error) from None
    refs = [row[0] for row in rows]
    vectors = np.asarray([np.asarray(row[1].to_numpy() if hasattr(row[1], "to_numpy") else row[1])
                          for row in rows], dtype=np.float32).reshape(len(rows), config.EMBED_DIM)
    return vectors, refs, want_model


def nearest(query: np.ndarray, limit: int, want_model: str, want_provider: str) -> list[tuple[float, dict]]:
    """코사인 거리(<=>)가 가까운 순서로 (유사도, 문단). 벡터는 저장 때 정규화돼 있어요."""
    sql = _sql()
    try:
        with pool().connection() as conn:
            _check_meta(conn, want_model, want_provider)
            rows = conn.execute(sql.SQL("""
                SELECT doc, 1 - (embedding <=> %s) AS similarity
                FROM {}.reference_chunk ORDER BY embedding <=> %s, position LIMIT %s""").format(_schema()),
                (query, query, limit)).fetchall()
    except SystemExit:
        raise
    except Exception as error:
        raise _connect_error(error) from None
    return [(float(similarity), doc) for doc, similarity in rows]


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
