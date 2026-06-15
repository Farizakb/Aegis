"""pgvector cosine-similarity search over the `chunks` table."""

from __future__ import annotations

from pgvector import Vector

from agent.state import RetrievedChunk
from retrieval.embeddings import embed_texts


def search(conn, query: str, top_k: int = 5) -> list[RetrievedChunk]:
    [embedding] = embed_texts([query])
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source, content, 1 - (embedding <=> %s) AS score
            FROM chunks
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            (Vector(embedding), Vector(embedding), top_k),
        )
        rows = cur.fetchall()

    return [RetrievedChunk(source=row[0], content=row[1], score=float(row[2])) for row in rows]
