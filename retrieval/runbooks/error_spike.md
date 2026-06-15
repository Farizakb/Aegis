# Runbook: error_spike

## Symptoms
- `aegis:incidents` shows an `error_spike` incident on `mock_app` with `severity=error`.
- `sample_events[].message` reads `"5xx rate {pct}%"` where `pct` hovers around 50%.
- Unlike `memory_leak` and `db_deadlock`, this fault is "real": while active, `GET /work`
  genuinely returns HTTP 500 for roughly half of all requests.

## Root cause
`ErrorSpikeFault` (`mock_app/faults/error_spike.py`) exposes `should_fail()`, called from the
`/work` endpoint. While the fault is active, each call increments `self._total` and, with
probability `ERROR_RATE = 0.5`, also increments `self._errors` and returns `True` (causing
`/work` to respond with a 500). `emit_signals` periodically reports the running error rate
(`self._errors / self._total * 100`). This models a downstream dependency that has become
unreliable — e.g. a flaky upstream service or a bad deploy — with no protection on the calling
side.

Key constants:
- `ERROR_RATE = 0.5` — fraction of `/work` calls that fail while active.
- `BUCKET_SIZE_PCT = 10` — dedup bucket granularity for the emitted error-rate metric.

## Diagnosis steps
1. Confirm `incident.fault_kind == "error_spike"` and `severity == "error"`.
2. Read `sample_events[].metric` — error rate should be roughly stable around 40-60%.
3. Inspect `mock_app/main.py`'s `/work` handler and `ErrorSpikeFault.should_fail()` — every
   request is sent straight through with no retry, fallback, or circuit breaker.

## Recommended fix
Add resilience to the `/work` call path rather than (or in addition to) fixing the upstream
dependency:
- Circuit breaker: once the error rate over a rolling window exceeds a threshold, short-circuit
  `/work` to a fast fallback response instead of calling through, and periodically probe to
  see if the dependency has recovered.
- Retry with backoff for transient failures, bounded so retries don't amplify load on an
  already-struggling dependency.

The patch should target `mock_app/main.py` (the `/work` handler) and/or
`mock_app/faults/error_spike.py` (`should_fail`), depending on whether the fix is framed as
"protect the caller" or "stabilize the simulated dependency".
