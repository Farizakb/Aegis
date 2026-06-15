# Runbook: memory_leak

## Symptoms
- `aegis:incidents` shows a `memory_leak` incident on `mock_app` with a steadily increasing
  `metric` value (reported RSS in MB) across `sample_events`.
- Severity starts at `warning` and escalates to `critical` once RSS crosses **150MB**.
- The incident's `event_count` keeps growing for as long as the fault stays active — there is
  no natural ceiling.

## Root cause
`MemoryLeakFault` (`mock_app/faults/memory_leak.py`) appends a new 5MB chunk
(`bytes(CHUNK_SIZE_MB * 1024 * 1024)`) to an in-memory list (`self._chunks`) on every emit tick
while the fault is active, and never evicts old chunks. `clear()` is the only thing that empties
the list. This models an unbounded in-memory cache / buffer that grows without a retention
policy — a classic "list that only grows" memory leak.

Key constants:
- `CHUNK_SIZE_MB = 5` — growth per tick.
- `CRITICAL_THRESHOLD_MB = 150` — severity escalation point (30 ticks of growth).
- `BUCKET_SIZE_MB = 50` — dedup bucket granularity for the emitted metric.

## Diagnosis steps
1. Read `incident.sample_events[].metric` — confirm RSS is monotonically increasing tick over
   tick (≈ +5MB per sample).
2. Confirm `incident.severity` is `critical` (RSS ≥ 150MB) vs `warning` (still climbing but
   under threshold) — this affects urgency but not the fix.
3. Inspect `mock_app/faults/memory_leak.py` — the unbounded `self._chunks` list is the leak.

## Recommended fix
Bound the growth so RSS plateaus instead of climbing forever:
- Cap `self._chunks` length (e.g. evict the oldest chunk once `len(self._chunks) >=
  CRITICAL_THRESHOLD_MB // CHUNK_SIZE_MB`), turning the unbounded list into a fixed-size
  ring buffer.
- Alternatively, stop appending once the critical threshold is reached and emit a single
  `critical` event rather than continuing to grow.

The patch should target `mock_app/faults/memory_leak.py`, specifically the `emit_signals`
method where `self._chunks.append(...)` is called.
