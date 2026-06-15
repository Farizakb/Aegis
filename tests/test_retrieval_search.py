import pytest

from retrieval.db import get_conn
from retrieval.ingest import Document, chunk_documents, ingest
from retrieval.search import search


@pytest.mark.integration
def test_search_returns_relevant_chunk_ranked_first():
    conn = get_conn()
    try:
        docs = chunk_documents(
            [
                Document(
                    id="test:memory",
                    source="test:memory",
                    content="Memory leak: unbounded list growth causes RSS to climb until OOM.",
                ),
                Document(
                    id="test:deadlock",
                    source="test:deadlock",
                    content="Database deadlock: two transactions waiting on each other's locks.",
                ),
            ]
        )
        ingest(conn, docs)

        results = search(conn, "rss climbing memory growth", top_k=2)

        assert results[0].source == "test:memory"
        assert results[0].score >= results[1].score
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE id LIKE 'test:%'")
        conn.commit()
        conn.close()
