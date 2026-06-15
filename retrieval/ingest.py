"""Load runbooks + git history, chunk, embed, and upsert into the pgvector `chunks` table."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from pgvector import Vector

from retrieval.db import get_conn
from retrieval.embeddings import embed_texts

RUNBOOKS_DIR = Path(__file__).parent / "runbooks"
REPO_ROOT = Path(__file__).resolve().parents[1]

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


@dataclass
class Document:
    id: str
    source: str
    content: str


def load_runbooks(dir_path: Path = RUNBOOKS_DIR) -> list[Document]:
    docs = []
    for path in sorted(dir_path.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        docs.append(Document(id=f"runbook:{path.name}", source=f"runbook:{path.name}", content=content))
    return docs


def load_git_history(repo_path: Path = REPO_ROOT, max_commits: int = 50) -> list[Document]:
    """One Document per commit: subject, body, and --stat diffstat. Real history, not fabricated."""
    log = subprocess.run(
        ["git", "log", f"-{max_commits}", "--format=%H"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    hashes = [line for line in log.stdout.splitlines() if line]

    docs = []
    for sha in hashes:
        show = subprocess.run(
            ["git", "show", "--stat", "--format=commit %H%nSubject: %s%n%n%b", sha],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        short_sha = sha[:12]
        docs.append(Document(id=f"commit:{short_sha}", source=f"commit:{short_sha}", content=show.stdout.strip()))
    return docs


def chunk_documents(docs: list[Document], chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[Document]:
    chunks: list[Document] = []
    for doc in docs:
        text = doc.content
        if len(text) <= chunk_size:
            chunks.append(doc)
            continue

        start = 0
        idx = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunks.append(Document(id=f"{doc.id}#{idx}", source=doc.source, content=text[start:end]))
            if end == len(text):
                break
            start = end - overlap
            idx += 1
    return chunks


def ingest(conn, docs: list[Document]) -> None:
    if not docs:
        return
    embeddings = embed_texts([d.content for d in docs])
    with conn.cursor() as cur:
        for doc, embedding in zip(docs, embeddings):
            cur.execute(
                """
                INSERT INTO chunks (id, source, content, embedding)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET source = EXCLUDED.source, content = EXCLUDED.content, embedding = EXCLUDED.embedding
                """,
                (doc.id, doc.source, doc.content, Vector(embedding)),
            )
    conn.commit()


def main() -> None:
    docs = chunk_documents(load_runbooks() + load_git_history())
    conn = get_conn()
    try:
        ingest(conn, docs)
        print(f"Ingested {len(docs)} chunks into pgvector")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
