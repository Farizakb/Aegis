import pytest

from mock_app.controls import FLAGS, RISKY_FLAG, SCALE, FeatureFlags, ScaleState


def test_flags_default_off():
    flags = FeatureFlags()
    assert flags.is_enabled(RISKY_FLAG) is False
    assert flags.all() == {RISKY_FLAG: False}


def test_flag_set_and_unknown_flag_raises():
    flags = FeatureFlags()
    flags.set(RISKY_FLAG, True)
    assert flags.is_enabled(RISKY_FLAG) is True
    with pytest.raises(KeyError):
        flags.set("no_such_flag", True)


def test_scale_defaults_and_bounds():
    scale = ScaleState(workers=2)
    assert scale.workers == 2
    scale.set_workers(8)
    assert scale.workers == 8
    with pytest.raises(ValueError):
        scale.set_workers(0)
    with pytest.raises(ValueError):
        scale.set_workers(17)


def test_scale_env_misconfig_clamped_not_crashing(monkeypatch):
    monkeypatch.setenv("WORKERS", "0")
    assert ScaleState().workers == 1
    monkeypatch.setenv("WORKERS", "99")
    assert ScaleState().workers == 16
    monkeypatch.setenv("WORKERS", "not-a-number")
    assert ScaleState().workers == 2


def test_flags_endpoints(client):
    assert client.get("/flags").json() == {RISKY_FLAG: False}
    resp = client.post(f"/flags/{RISKY_FLAG}", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json() == {"name": RISKY_FLAG, "enabled": True}
    assert client.get("/flags").json() == {RISKY_FLAG: True}
    assert client.post("/flags/no_such_flag", json={"enabled": True}).status_code == 404


def test_scale_endpoints(client):
    assert client.get("/scale").json() == {"workers": 2}
    resp = client.post("/scale", json={"workers": 6})
    assert resp.status_code == 200
    assert resp.json() == {"workers": 6}
    assert client.post("/scale", json={"workers": 0}).status_code == 422
    assert client.post("/scale", json={"workers": 99}).status_code == 422
