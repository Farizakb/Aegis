"""Postgres + pgvector connection and schema for the retrieval corpus."""

from __future__ import annotations

import os

import psycopg
from pgvector.psycopg import register_vector

from retrieval.embeddings import EMBEDDING_DIM

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector({EMBEDDING_DIM}) NOT NULL
);
"""


def conn_string() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "aegis")
    password = os.environ.get("POSTGRES_PASSWORD", "aegis")
    db = os.environ.get("POSTGRES_DB", "aegis")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def get_conn(conn_str: str | None = None) -> psycopg.Connection:
    conn = psycopg.connect(conn_str or conn_string())
    ensure_schema(conn)
    register_vector(conn)
    return conn


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()
