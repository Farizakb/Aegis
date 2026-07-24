"""Tests for `--tier all` orchestration and the shared `_dependency_status` skip
matrix (Phase 6 Task 9). Dependency-free: the tier-1 gate is pure Python, and
`_dependency_status` is monkeypatched so tiers 2-4 always self-skip here."""
from __future__ import annotations

import argparse

from evals.run_evals import _dependency_status, _dispatch


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
