# evals/incidents/_generate.py
"""One-shot: emit schema-authentic IncidentEvent fixtures. Re-run to regenerate."""
from __future__ import annotations
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

from stream.schema import FaultKind, IncidentEvent, RawEvent, Severity

OUT = Path(__file__).parent
T0 = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)

_seq = itertools.count(1)


def _raw(kind, sev, msg, metric):
    i = next(_seq)
    return RawEvent(event_id=f"{kind.value}-{i}", fault_kind=kind, severity=sev,
                    source="mock_app", message=msg, metric=metric, ts=T0,
                    dedup_key=f"{kind.value}:{i}")


def _incident(iid, kind, sev, title, summary, samples, duplicate_count):
    return IncidentEvent(
        incident_id=iid, fault_kind=kind, severity=sev, source="mock_app",
        title=title, summary=summary, first_seen=T0, last_seen=T0,
        event_count=len(samples), duplicate_count=duplicate_count, sample_events=samples,
        correlation_window_s=30.0,
    )


FIXTURES = [
    # --- memory_leak x5 (memory_leak_05 is the deliberately low-confidence / ambiguous case) ---
    ("memory_leak_01", _incident(
        "memory_leak_01", FaultKind.memory_leak, Severity.critical,
        "RSS climbing on mock_app",
        "Resident memory grows ~5MB/tick and does not plateau; RSS has crossed the critical threshold.",
        [_raw(FaultKind.memory_leak, Severity.critical, "rss_mb=812 and rising", 812.0),
         _raw(FaultKind.memory_leak, Severity.critical, "rss_mb=817 and rising", 817.0),
         _raw(FaultKind.memory_leak, Severity.critical, "rss_mb=822 and rising", 822.0)],
        duplicate_count=6)),

    ("memory_leak_02", _incident(
        "memory_leak_02", FaultKind.memory_leak, Severity.warning,
        "RSS trending upward on mock_app",
        "Resident memory has grown roughly 40MB over the last several ticks; still below the critical threshold.",
        [_raw(FaultKind.memory_leak, Severity.warning, "rss_mb=110 and rising", 110.0),
         _raw(FaultKind.memory_leak, Severity.warning, "rss_mb=115 and rising", 115.0),
         _raw(FaultKind.memory_leak, Severity.warning, "rss_mb=120 and rising", 120.0)],
        duplicate_count=3)),

    ("memory_leak_03", _incident(
        "memory_leak_03", FaultKind.memory_leak, Severity.critical,
        "Sustained RSS growth nearing OOM on mock_app",
        "RSS has grown continuously for an extended window and is now well past the critical threshold.",
        [_raw(FaultKind.memory_leak, Severity.critical, "rss_mb=905 and rising", 905.0),
         _raw(FaultKind.memory_leak, Severity.critical, "rss_mb=910 and rising", 910.0),
         _raw(FaultKind.memory_leak, Severity.critical, "rss_mb=915 and rising", 915.0)],
        duplicate_count=20)),

    ("memory_leak_04", _incident(
        "memory_leak_04", FaultKind.memory_leak, Severity.critical,
        "Memory growth resumed after brief plateau on mock_app",
        "RSS growth paused briefly then resumed, crossing the critical threshold again on the latest tick.",
        [_raw(FaultKind.memory_leak, Severity.critical, "rss_mb=160 and rising", 160.0),
         _raw(FaultKind.memory_leak, Severity.critical, "rss_mb=165 and rising", 165.0),
         _raw(FaultKind.memory_leak, Severity.critical, "rss_mb=170 and rising", 170.0)],
        duplicate_count=9)),

    ("memory_leak_05", _incident(
        "memory_leak_05", FaultKind.memory_leak, Severity.warning,
        "Intermittent RSS fluctuation on mock_app",
        "RSS rose then partially dipped across a noisy two-sample window; the signal is inconsistent "
        "with a monotonic leak and could plausibly be transient GC activity rather than a genuine leak.",
        [_raw(FaultKind.memory_leak, Severity.warning,
              "rss_mb=130 and rising, though the prior tick briefly dipped", 130.0),
         _raw(FaultKind.memory_leak, Severity.warning,
              "rss_mb=125, lower than the previous tick", 125.0)],
        duplicate_count=1)),

    # --- db_deadlock x4 ---
    ("db_deadlock_01", _incident(
        "db_deadlock_01", FaultKind.db_deadlock, Severity.error,
        "Lock contention rising on mock_app",
        "Transactions are timing out waiting on locks; wait time is growing every other tick.",
        [_raw(FaultKind.db_deadlock, Severity.error, "deadlock detected on txn 14", 600.0),
         _raw(FaultKind.db_deadlock, Severity.error, "deadlock detected on txn 15", 700.0)],
        duplicate_count=4)),

    ("db_deadlock_02", _incident(
        "db_deadlock_02", FaultKind.db_deadlock, Severity.error,
        "Escalating lock waits on mock_app",
        "Lock-wait time continues to climb with no timeout or retry logic in place.",
        [_raw(FaultKind.db_deadlock, Severity.error, "deadlock detected on txn 40", 1200.0),
         _raw(FaultKind.db_deadlock, Severity.error, "deadlock detected on txn 41", 1300.0)],
        duplicate_count=18)),

    ("db_deadlock_03", _incident(
        "db_deadlock_03", FaultKind.db_deadlock, Severity.error,
        "Early-stage lock contention on mock_app",
        "Lock waits have just begun climbing; still low magnitude but trending upward.",
        [_raw(FaultKind.db_deadlock, Severity.error, "deadlock detected on txn 3", 500.0),
         _raw(FaultKind.db_deadlock, Severity.error, "deadlock detected on txn 4", 600.0)],
        duplicate_count=1)),

    ("db_deadlock_04", _incident(
        "db_deadlock_04", FaultKind.db_deadlock, Severity.error,
        "Severe, long-running lock contention on mock_app",
        "Lock-wait time has been climbing for an extended window and queries are now failing outright.",
        [_raw(FaultKind.db_deadlock, Severity.error, "deadlock detected on txn 88", 2200.0),
         _raw(FaultKind.db_deadlock, Severity.error, "deadlock detected on txn 89", 2300.0)],
        duplicate_count=30)),

    # --- error_spike x4 ---
    ("error_spike_01", _incident(
        "error_spike_01", FaultKind.error_spike, Severity.error,
        "5xx rate spiking on mock_app",
        "Roughly half of /work calls are failing with HTTP 500 while the fault is active.",
        [_raw(FaultKind.error_spike, Severity.error, "5xx rate 48%", 48.0),
         _raw(FaultKind.error_spike, Severity.error, "5xx rate 52%", 52.0)],
        duplicate_count=10)),

    ("error_spike_02", _incident(
        "error_spike_02", FaultKind.error_spike, Severity.error,
        "5xx rate elevated on mock_app, tied to risky_feature flag",
        "Error rate rose immediately after the risky_feature flag was enabled.",
        [_raw(FaultKind.error_spike, Severity.error, "5xx rate 45%", 45.0),
         _raw(FaultKind.error_spike, Severity.error, "5xx rate 50%", 50.0)],
        duplicate_count=5)),

    ("error_spike_03", _incident(
        "error_spike_03", FaultKind.error_spike, Severity.error,
        "5xx rate severely elevated on mock_app",
        "Error rate has climbed above 60% and is not self-recovering.",
        [_raw(FaultKind.error_spike, Severity.error, "5xx rate 60%", 60.0),
         _raw(FaultKind.error_spike, Severity.error, "5xx rate 65%", 65.0)],
        duplicate_count=14)),

    ("error_spike_04", _incident(
        "error_spike_04", FaultKind.error_spike, Severity.error,
        "5xx rate marginally elevated on mock_app",
        "Error rate is hovering just above baseline but consistently nonzero across samples.",
        [_raw(FaultKind.error_spike, Severity.error, "5xx rate 40%", 40.0),
         _raw(FaultKind.error_spike, Severity.error, "5xx rate 42%", 42.0)],
        duplicate_count=2)),

    # --- traffic_surge x5 ---
    ("traffic_surge_01", _incident(
        "traffic_surge_01", FaultKind.traffic_surge, Severity.warning,
        "Latency degrading under load on mock_app",
        "p95 latency is elevated above baseline but below the degraded threshold.",
        [_raw(FaultKind.traffic_surge, Severity.warning, "p95 latency 100ms under load", 100.0),
         _raw(FaultKind.traffic_surge, Severity.warning, "p95 latency 105ms under load", 105.0)],
        duplicate_count=3)),

    ("traffic_surge_02", _incident(
        "traffic_surge_02", FaultKind.traffic_surge, Severity.error,
        "Latency degraded past threshold on mock_app",
        "p95 latency has crossed the degraded threshold under sustained synthetic load.",
        [_raw(FaultKind.traffic_surge, Severity.error, "p95 latency 180ms under load", 180.0),
         _raw(FaultKind.traffic_surge, Severity.error, "p95 latency 185ms under load", 185.0)],
        duplicate_count=8)),

    ("traffic_surge_03", _incident(
        "traffic_surge_03", FaultKind.traffic_surge, Severity.error,
        "Severe latency degradation on mock_app",
        "p95 latency is far above baseline; the worker pool is undersized for current load.",
        [_raw(FaultKind.traffic_surge, Severity.error, "p95 latency 220ms under load", 220.0),
         _raw(FaultKind.traffic_surge, Severity.error, "p95 latency 225ms under load", 225.0)],
        duplicate_count=16)),

    ("traffic_surge_04", _incident(
        "traffic_surge_04", FaultKind.traffic_surge, Severity.warning,
        "Latency borderline under load on mock_app",
        "p95 latency is close to the degraded threshold but has not yet crossed it.",
        [_raw(FaultKind.traffic_surge, Severity.warning, "p95 latency 130ms under load", 130.0),
         _raw(FaultKind.traffic_surge, Severity.warning, "p95 latency 135ms under load", 135.0)],
        duplicate_count=1)),

    ("traffic_surge_05", _incident(
        "traffic_surge_05", FaultKind.traffic_surge, Severity.error,
        "Sustained severe latency surge on mock_app",
        "p95 latency has remained far above baseline across an extended window.",
        [_raw(FaultKind.traffic_surge, Severity.error, "p95 latency 260ms under load", 260.0),
         _raw(FaultKind.traffic_surge, Severity.error, "p95 latency 265ms under load", 265.0)],
        duplicate_count=22)),
]


def main():
    for name, incident in FIXTURES:
        (OUT / f"{name}.json").write_text(
            json.dumps(incident.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(FIXTURES)} fixtures")


if __name__ == "__main__":
    main()
