"""agent.main pure helpers: promoted-run identity so promotion reports don't
overwrite the original mitigation run's row in Postgres."""

from agent.main import promoted_incident
from tests.test_agent_nodes import make_incident


def test_promoted_incident_suffixes_id_and_preserves_other_fields():
    incident = make_incident(incident_id="inc-1")

    promoted = promoted_incident(incident)

    assert promoted.incident_id == "inc-1#promo"
    assert promoted.model_dump(exclude={"incident_id"}) == incident.model_dump(exclude={"incident_id"})


def test_promoted_incident_does_not_mutate_original():
    incident = make_incident(incident_id="inc-1")

    promoted_incident(incident)

    assert incident.incident_id == "inc-1"
