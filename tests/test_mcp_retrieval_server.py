import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from retrieval.db import get_conn
from retrieval.ingest import Document, chunk_documents, ingest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
async def test_search_knowledge_tool_returns_chunks():
    conn = get_conn()
    try:
        docs = chunk_documents(
            [
                Document(
                    id="test:mcp_memory",
                    source="test:mcp_memory",
                    content="Memory leak: unbounded list growth causes RSS to climb until OOM.",
                )
            ]
        )
        ingest(conn, docs)
    finally:
        conn.close()

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tools.retrieval_server"],
        cwd=str(REPO_ROOT),
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("search_knowledge", arguments={"query": "memory leak rss", "top_k": 3})

        assert result.structuredContent is not None
        sources = [c["source"] for c in result.structuredContent["result"]]
        assert "test:mcp_memory" in sources
    finally:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE id LIKE 'test:%'")
        conn.commit()
        conn.close()
