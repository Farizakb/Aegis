from mock_app.controls import RISKY_FLAG


def test_healthz_baseline_shape(client):
    data = client.get("/healthz").json()
    assert data["status"] == "ok"
    assert data["workers"] == 2
    assert data["flags"] == {RISKY_FLAG: False}
    assert data["metrics"] == {}


def test_healthz_reports_traffic_surge_latency(client):
    client.post("/faults/traffic_surge/trigger")
    data = client.get("/healthz").json()
    assert data["metrics"]["latency_ms"] == 200.0   # workers=2 default

    client.post("/scale", json={"workers": 8})
    data = client.get("/healthz").json()
    assert data["metrics"]["latency_ms"] == 50.0    # recovered — the sandbox's assertion


def test_healthz_reports_error_rate(client):
    client.post("/faults/error_spike/trigger")
    for _ in range(20):
        client.get("/work")
    data = client.get("/healthz").json()
    assert "error_rate_pct" in data["metrics"]
    assert 0.0 <= data["metrics"]["error_rate_pct"] <= 100.0


def test_healthz_metrics_clear_with_fault(client):
    client.post("/faults/traffic_surge/trigger")
    client.post("/faults/traffic_surge/clear")
    assert client.get("/healthz").json()["metrics"] == {}
