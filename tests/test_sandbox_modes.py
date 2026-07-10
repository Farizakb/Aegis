import time

from fastapi.testclient import TestClient

import mock_app.main as main_module


def test_stream_disabled_emit_loop_ticks_without_redis(monkeypatch):
    monkeypatch.setenv("AEGIS_STREAM_DISABLED", "1")
    monkeypatch.setattr(main_module, "EMIT_INTERVAL_S", 0.05)
    # No fakeredis patching here on purpose: startup and ticking must not need Redis.
    with TestClient(main_module.app) as client:
        client.post("/faults/memory_leak/trigger")
        time.sleep(0.5)
        rss = client.get("/healthz").json()["metrics"].get("rss_mb", 0.0)
    # >= 2 ticks proves the loop survived publishing (NullProducer), not just the first append
    assert rss >= 10.0


def test_previous_version_trigger_noop_for_last_deploy_bugs(monkeypatch, client):
    monkeypatch.setenv("APP_VERSION", "previous")

    resp = client.post("/faults/db_deadlock/trigger")
    assert resp.status_code == 200
    assert resp.json() == {"kind": "db_deadlock", "active": False}

    resp = client.post("/faults/error_spike/trigger")
    assert resp.json() == {"kind": "error_spike", "active": False}
    assert client.get("/flags").json() == {"risky_feature": False}

    # /work never fails on the previous version
    assert all(client.get("/work").status_code == 200 for _ in range(30))


def test_previous_version_still_reproduces_latent_faults(monkeypatch, client):
    monkeypatch.setenv("APP_VERSION", "previous")

    assert client.post("/faults/memory_leak/trigger").json()["active"] is True
    assert client.post("/faults/traffic_surge/trigger").json()["active"] is True


def test_current_version_trigger_unchanged(client):
    assert client.post("/faults/db_deadlock/trigger").json() == {
        "kind": "db_deadlock", "active": True,
    }
