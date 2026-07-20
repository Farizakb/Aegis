"""MCP stdio server exposing retrieval over the pgvector knowledge base."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from retrieval.db import get_conn
from retrieval.search import search

mcp = FastMCP("aegis-retrieval")


@mcp.tool()
def search_knowledge(query: str, top_k: int = 5) -> list[dict]:
    """Search runbooks and git commit history for context relevant to an incident."""
    conn = get_conn()
    try:
        chunks = search(conn, query, top_k)
    finally:
        conn.close()
    return [chunk.model_dump() for chunk in chunks]


if __name__ == "__main__":
    # Warm the embedding model on the main thread before the stdio event loop
    # starts. Loading SentenceTransformer/torch lazily inside FastMCP's tool
    # worker on the first request deadlocks under stdio; a startup warm-up makes
    # search_knowledge a fast cached encode.
    from retrieval.embeddings import get_embedder

    get_embedder()
    mcp.run(transport="stdio")
