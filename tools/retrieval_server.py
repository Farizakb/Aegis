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
    mcp.run(transport="stdio")
