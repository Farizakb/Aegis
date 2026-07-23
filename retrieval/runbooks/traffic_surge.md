# Runbook: traffic_surge

## Symptoms
- `aegis:incidents` shows a `traffic_surge` incident on `mock_app` with `sample_events[].message`
  reading `"p95 latency {n}ms under load"`.
- `sample_events[].metric` (p95 latency in ms) is elevated relative to baseline and does not
  fall on its own while the fault stays active.
- Severity is `warning` while latency is above baseline but below **150ms**, and escalates to
  `error` once latency crosses that threshold.

## Root cause
`TrafficSurgeFault` (`mock_app/faults/traffic_surge.py`) models a fixed amount of synthetic
concurrent load (`LOAD_UNITS = 8`) hitting a worker pool of size `SCALE.workers`. Latency is
computed deterministically as `BASE_LATENCY_MS * LOAD_UNITS / SCALE.workers` — i.e. load per
worker, not a leak or a stuck resource. This models real capacity exhaustion: demand has
outstripped the number of workers available to serve it.

Key constants:
- `BASE_LATENCY_MS = 50.0` — latency at full capacity (load == workers).
- `LOAD_UNITS = 8` — synthetic concurrent load while the surge is active.
- `DEGRADED_THRESHOLD_MS = 150.0` — severity escalation point.
- `BUCKET_SIZE_MS = 50` — dedup bucket granularity for the emitted metric.

## Diagnosis steps
1. Confirm `incident.fault_kind == "traffic_surge"` and read `sample_events[].metric` — p95
   latency should be stable-but-elevated (a ratio of load to worker count), not monotonically
   climbing the way `memory_leak`'s RSS does.
2. Check `mock_app/controls.py`'s `SCALE.workers` — low worker count relative to `LOAD_UNITS`
   is the entire cause; there is no leaking resource or stuck lock to find.
3. Inspect `mock_app/faults/traffic_surge.py` — `latency_ms` is a pure function of
   `SCALE.workers`, confirming this is a capacity problem, not a defect in application code.

## Recommended fix
**`scale_out` is the mitigation** — increasing `SCALE.workers` directly and proportionally
lowers `latency_ms` back toward `BASE_LATENCY_MS`, because the fault model divides load by
worker count.

**`restart_service` makes this worse, not better.** A restart does not change `SCALE.workers`;
it briefly drops the worker pool to zero while the process comes back up, which momentarily
maximizes load-per-worker (kills whatever warm capacity existed) and spikes latency further
before settling back to the same degraded ratio. Restart is never an acceptable mitigation for
`traffic_surge`.

There is **no durable code fix** to file here — nothing in `mock_app` is defective; the fault is
a capacity/provisioning problem, not a bug. The correct long-term action is capacity planning
(more workers, autoscaling policy), not a patch.
