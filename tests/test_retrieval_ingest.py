from retrieval.ingest import REPO_ROOT, Document, chunk_documents, load_git_history, load_runbooks


def test_load_runbooks_reads_all_four_fault_runbooks():
    docs = load_runbooks()

    ids = {d.id for d in docs}
    assert ids == {
        "runbook:db_deadlock.md",
        "runbook:error_spike.md",
        "runbook:memory_leak.md",
        "runbook:traffic_surge.md",
    }
    for doc in docs:
        assert doc.source == doc.id
        assert len(doc.content) > 0


def test_load_git_history_returns_real_commits():
    docs = load_git_history(repo_path=REPO_ROOT, max_commits=5)

    assert len(docs) >= 1
    for doc in docs:
        assert doc.id.startswith("commit:")
        assert doc.content.startswith("commit ")
        assert "Subject:" in doc.content


def test_chunk_documents_passes_short_docs_through_unchanged():
    docs = [Document(id="a", source="a", content="short text")]

    chunks = chunk_documents(docs, chunk_size=800, overlap=100)

    assert chunks == docs


def test_chunk_documents_splits_long_docs_with_overlap():
    text = "x" * 2000
    docs = [Document(id="a", source="a", content=text)]

    chunks = chunk_documents(docs, chunk_size=800, overlap=100)

    assert len(chunks) > 1
    assert all(c.source == "a" for c in chunks)
    assert all(c.id.startswith("a#") for c in chunks)
    # last chunk reaches the end of the text
    assert chunks[-1].content == text[-len(chunks[-1].content):]
    # overlap: end of one chunk and start of the next share content
    first_chunk_end = chunks[0].content[-100:]
    second_chunk_start = chunks[1].content[:100]
    assert first_chunk_end == second_chunk_start
