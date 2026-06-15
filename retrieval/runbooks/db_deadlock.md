# Runbook: db_deadlock

## Symptoms
- `aegis:incidents` shows a `db_deadlock` incident on `mock_app` with `severity=error` for
  every sample event.
- `sample_events[].message` reads `"deadlock detected on txn {n}"` with `n` increasing.
- `sample_events[].metric` (lock-wait time in ms) increases by 100ms every *other* tick — the
  signal only fires on even ticks, so the correlation window sees roughly half as many raw
  events as ticks elapsed.

## Root cause
`DbDeadlockFault` (`mock_app/faults/db_deadlock.py`) simulates growing lock contention: each
emit tick increments an internal counter `self._tick`, and on every even tick it emits a
`RawEvent` with `lock_wait_ms = BASE_LOCK_WAIT_MS + (self._tick * LOCK_WAIT_STEP_MS)`. Because
the wait time grows linearly and unboundedly with tick count, this models a transaction that
never releases its lock — i.e. two transactions perpetually waiting on each other's locks
(classic deadlock), with no timeout or deadlock-detection/retry logic in place.

Key constants:
- `BASE_LOCK_WAIT_MS = 500` — starting wait time.
- `LOCK_WAIT_STEP_MS = 100` — growth per even tick.
- `BUCKET_SIZE_MS = 500` — dedup bucket granularity for the emitted metric.

## Diagnosis steps
1. Confirm `incident.fault_kind == "db_deadlock"` and `severity == "error"`.
2. Read `sample_events[].metric` — lock-wait time should increase by ~200ms per sample (since
   only even ticks emit).
3. Inspect `mock_app/faults/db_deadlock.py` — there is no timeout, backoff, or lock-ordering
   logic; `lock_wait_ms` grows without bound while `self._active` is true.

## Recommended fix
Introduce a bounded wait with timeout + retry/backoff instead of letting lock-wait grow
unboundedly:
- Cap `lock_wait_ms` at a maximum (e.g. `BASE_LOCK_WAIT_MS + N * LOCK_WAIT_STEP_MS` for a fixed
  `N`), then have the transaction "time out" — emit a `critical` event once and stop escalating,
  modeling a deadlock-timeout abort + retry.
- More generally (for a real DB), the fix direction is: enforce consistent lock acquisition
  ordering across transactions, add statement/lock timeouts, and retry with exponential
  backoff on deadlock errors (e.g. Postgres `40P01`).

The patch should target `mock_app/faults/db_deadlock.py`, specifically the `emit_signals`
method where `lock_wait_ms` is computed.
