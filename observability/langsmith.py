"""LangSmith: env-gated LLM tracing (auto no-op without LANGCHAIN_API_KEY)."""

from __future__ import annotations

import os


def setup_langsmith() -> bool:
    """Return whether LangSmith tracing is active. No code path forces it on;
    LangChain reads LANGCHAIN_* env vars itself and no-ops when the key absent."""
    enabled = (
        os.environ.get("LANGCHAIN_TRACING_V2", "").lower() in ("1", "true")
        and bool(os.environ.get("LANGCHAIN_API_KEY"))
    )
    return enabled


def experiment_env(tier, model_config: dict) -> dict:
    """Env overrides that group this eval run under a named LangSmith project.
    Returns {} (no-op) unless LangSmith is enabled - local JSON stays the source
    of truth; this only makes runs findable in the LangSmith UI when it's on."""
    if not setup_langsmith():
        return {}
    triage_model = model_config.get("triage", "unknown")
    return {"LANGCHAIN_PROJECT": f"aegis-eval-tier{tier}-{triage_model}"}


def run_config(incident, trace_id: str | None) -> dict:
    """LangGraph .ainvoke config that tags the run for LangSmith + Jaeger cross-link."""
    return {
        "run_name": f"incident-{incident.incident_id}",
        "metadata": {
            "incident_id": incident.incident_id,
            "fault_kind": incident.fault_kind.value,
            "trace_id": trace_id,
        },
    }
