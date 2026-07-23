# Aegis — Self-Healing Incident-Response Agent

> ⚠️ **Work in progress.** Core pipeline plus end-to-end observability are built and run
> (Phases 1–5); the eval pyramid and dashboard are next (Phases 6–7). This README is a
> placeholder and will be expanded when the project is complete.

Aegis is an agentic system that consumes incidents off a live event stream, decides on a
remediation, **proves the action actually clears the fault by replaying it in an isolated
sandbox**, gates it through a deterministic deny-by-default policy engine, asks a human for
approval with evidence, applies it, and reports the outcome — with the whole lifecycle
measured and traced.

It is built to demonstrate production-grade agent infrastructure rather than a chatbot demo:
real async ingestion, empirical verification of LLM proposals, auditable guardrails, and
human-in-the-loop safety.

## How it works

```
incident (Redis stream)
  → Triage      classify the fault + root-cause hypothesis + confidence   (LLM)
  → Retrieve    pull relevant runbooks / git history                       (PGVector RAG via MCP)
  → Propose     emit a RemediationPlan: mitigation now + durable fix later (LLM)
  → Sandbox     replay the fault in a disposable container, prove recovery (Docker)
      fail → re-propose with the failure evidence (may switch actions), max 3 attempts
  → Policy      deterministic 7-rule, deny-by-default gate                 (allow / needs_approval / block)
  → HITL        evidence-based approval brief with a TTL                   (web UI)
  → Apply       per-action applier restarts / rolls back / patches the live service
  → Report      persist an IncidentReport; file the durable fix as a follow-up
```

The **mitigation** runs the full pipeline now; the **durable fix** (a drafted code patch) is
filed as a follow-up and can be **promoted to its own pipeline run** with one click in the UI.

### Action catalog

`restart_service`, `rollback`, `toggle_feature_flag`, `scale_out`, `patch_code`, `escalate` —
each with declared safety metadata (blast radius, reversibility) that the policy engine consumes.

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Target app / faults | FastAPI (4 injectable, reproducible faults) |
| Event stream | Redis Streams |
| Agent orchestration | LangGraph |
| LLM | Anthropic Claude, tiered per node |
| Retrieval | PGVector on PostgreSQL (via an MCP tool) |
| Safe execution | Docker SDK — fault-replay sandbox |
| Policy engine | Custom deterministic rules (stateful) |
| HITL | Local FastAPI web UI |
| Tests | pytest |

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Typed contracts + mock-app faults | ✅ Done |
| 2 | Sandbox fault-replay | ✅ Done |
| 3 | Policy engine + tier-1 evals in CI | ✅ Done |
| 4 | Agent nodes, reflective retry, live appliers, HITL, reports | ✅ Done |
| 5 | Observability (OpenTelemetry + Jaeger + structlog + LangSmith) | ✅ Done |
| 6 | Eval pyramid + model-tiering experiment | ⏳ Planned |
| 7 | Streamlit dashboard + writeup | ⏳ Planned |

## Running it

Requires Docker Desktop and Python 3.11+.

```bash
# 1. Bring up the stack (target app, Redis, Postgres/pgvector, Jaeger)
docker compose up -d redis postgres app consumer jaeger

# 2. Seed the retrieval corpus (runbooks + git history)
python -m retrieval.ingest

# 3. Trigger a fault, then run the agent against the latest incident
#    (the agent runs on the host; the HITL UI serves at http://localhost:8001)
python -m agent.main --count 1
```

Set `ANTHROPIC_API_KEY` in a `.env` file first (the agent loads it on startup).
The **Jaeger UI** (one trace-waterfall per incident) serves at http://localhost:16686;
`python -m observability.demo` triggers a fault and prints the trace URL.

> **Host-run notes** (the agent runs on the host and spawns the retrieval MCP server as a
> stdio subprocess, so a few environment details matter):
>
> - **Run from a real console.** Use **PowerShell**, or `winpty python -m agent.main` under
>   Git Bash. The MCP stdio handshake is unreliable under Git Bash's *mintty* pseudo-console
>   on Windows and fails with `McpError: Connection closed`; a real console works.
> - **Keep `.env` hostname-free.** Host-run is zero-config — code defaults target the published
>   container ports. Remove any `REDIS_URL`, `POSTGRES_HOST`, or `LIVE_APP_URL` lines; those are
>   compose-only and resolve to unreachable container hostnames on the host.
> - **Set `HF_HUB_OFFLINE=1`** once the embedding model is cached, so the retrieval server loads
>   it offline instead of stalling on the Hugging Face Hub.
> - LangSmith LLM tracing is optional and off by default; set `LANGCHAIN_TRACING_V2=true` **and**
>   a `LANGCHAIN_API_KEY` to enable it (enabling the flag without a key produces 401 noise).

## Tests

```bash
# Fast suite (no Docker)
python -m pytest tests/ -m "not docker" -q

# Integration suite (needs Docker)
python -m pytest tests/ -m docker -q
```

---

*Portfolio project by Fariz Akbarzada. Detailed writeup, architecture diagram, and demo to follow.*
