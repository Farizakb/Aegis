# Runbook: error_spike

## Symptoms
- `aegis:incidents` shows an `error_spike` incident on `mock_app` with `severity=error`.
- `sample_events[].message` reads `"5xx rate {pct}%"` where `pct` hovers around 50%.
- Unlike `memory_leak` and `db_deadlock`, this fault is "real": while active, `GET /work`
  genuinely returns HTTP 500 for roughly half of all requests.

## Root cause
This fault is **flag-tied**. `ErrorSpikeFault` (`mock_app/faults/error_spike.py`) 500s on `/work`
**only while the `risky_feature` feature flag is on**. `trigger()` turns the flag on; `should_fail()`
short-circuits to `False` the moment the flag is off, so the 500s are gated entirely by the flag,
not by any in-process state. The flag ships as part of a deploy: `trigger()` no-ops when
`app_version() == "previous"`, i.e. the risky feature was introduced by the **most recent deploy**.

Key constants:
- `RISKY_FLAG = "risky_feature"` — the flag that gates the failing code path.
- `ERROR_RATE = 0.5` — fraction of `/work` calls that fail *while the flag is on*.
- `BUCKET_SIZE_PCT = 10` — dedup bucket granularity for the emitted error-rate metric.

## Diagnosis steps
1. Confirm `incident.fault_kind == "error_spike"` and `severity == "error"`.
2. Read `sample_events[].metric` — error rate should be roughly stable around 40-60%.
3. Check `GET /flags` — `risky_feature` will be `true`. This is the decisive signal: the 5xx are
   tied to that flag, even when the incident text does not mention it by name.

## Mitigation (do this NOW)
Prefer the cheapest reversible action that clears the fault immediately:
- **`toggle_feature_flag` (flag_name=`risky_feature`)** — the kill switch. Turning the flag off stops
  the 500s instantly, because `should_fail()` returns `False` whenever the flag is disabled. This is
  the primary mitigation.
- **`rollback`** — also valid: the risky feature shipped in the last deploy, so reverting to the
  previous version removes the flagged code path. Slightly heavier than the flag toggle but clears
  the same fault.

Do **not** `restart_service`: the failure is gated by the flag, not by in-process state, so a restart
leaves `risky_feature` on and the 5xx continue. Restart is the wrong reflex for a flag-tied spike.

## Durable fix (file as follow-up, do not apply now)
Once mitigated, draft a `patch_code` durable fix so the feature can be safely re-enabled: correct the
faulty code path behind `risky_feature` (in `mock_app/faults/error_spike.py` `should_fail`, and/or the
`/work` handler in `mock_app/main.py`) so it no longer 500s, and/or add resilience (circuit breaker /
bounded retry with backoff) on the calling side. The durable fix is never the on-call mitigation —
toggle the flag first, patch later.
