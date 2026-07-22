from observability.langsmith import run_config
from tests.test_agent_nodes import make_incident


def test_run_config_tags_incident_and_trace():
    cfg = run_config(make_incident(incident_id="inc-7"), trace_id="a" * 32)
    assert cfg["run_name"] == "incident-inc-7"
    assert cfg["metadata"]["incident_id"] == "inc-7"
    assert cfg["metadata"]["trace_id"] == "a" * 32
    assert "fault_kind" in cfg["metadata"]
