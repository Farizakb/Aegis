"""Tests for `--tier all` orchestration and the shared `_dependency_status` skip
matrix (Phase 6 Task 9). Dependency-free: the tier-1 gate is pure Python, and
`_dependency_status` is monkeypatched so tiers 2-4 always self-skip here."""
from __future__ import annotations

import argparse

from evals.run_evals import _dependency_status, _dispatch, _run_tier2_cli


def test_dependency_status_shape():
    status = _dependency_status()
    assert set(status.keys()) == {"anthropic_key", "postgres", "docker"}
    assert all(isinstance(v, bool) for v in status.values())


def test_tier_all_runs_tier1_and_skips_rest(monkeypatch, tmp_path, capsys):
    import evals.run_evals as run_evals_module

    monkeypatch.setattr(
        run_evals_module, "_dependency_status",
        lambda: {"anthropic_key": False, "postgres": False, "docker": False},
    )

    args = argparse.Namespace(tier="all", out_dir=str(tmp_path), tier3_all=False)
    exit_code = _dispatch(args)

    out = capsys.readouterr().out

    assert exit_code == 0

    written = sorted(p.name for p in tmp_path.iterdir())
    assert any(name.startswith("tier1-") for name in written)
    assert not any(name.startswith("tier2-") for name in written)
    assert not any(name.startswith("tier3-") for name in written)
    assert not any(name.startswith("tier4-") for name in written)

    assert "[tier2] SKIPPED" in out
    assert "[tier3] SKIPPED" in out
    assert "[tier4] SKIPPED" in out


def test_tier_cli_handles_connect_failure(monkeypatch, tmp_path, capsys):
    """A failed get_conn() (Postgres restart, connection limit, DDL/lock error
    between the _dependency_status probe and the real connect) must degrade to
    a report-only `[tier2] ERROR: ...` — never raise, never write a result.

    `_run_tier2_cli` does `from retrieval.db import get_conn` LOCALLY inside the
    function body, re-imported fresh on every call — so patching
    `evals.run_evals.get_conn` would not intercept it (no such module attribute
    exists). The real interception point is `retrieval.db.get_conn`."""
    import evals.run_evals as run_evals_module
    import retrieval.db as retrieval_db_module

    monkeypatch.setattr(
        run_evals_module, "_dependency_status",
        lambda: {"anthropic_key": True, "postgres": True, "docker": True},
    )

    def _boom():
        raise RuntimeError("pg down")

    monkeypatch.setattr(retrieval_db_module, "get_conn", _boom)

    args = argparse.Namespace(tier="2", out_dir=str(tmp_path), tier3_all=False)

    _run_tier2_cli(args)  # must not raise

    out = capsys.readouterr().out
    assert "[tier2] ERROR: pg down" in out

    written = sorted(p.name for p in tmp_path.iterdir()) if tmp_path.exists() else []
    assert not any(name.startswith("tier2-") for name in written)
