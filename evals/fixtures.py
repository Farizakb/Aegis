# evals/fixtures.py
"""Load the offline-replay incident corpus + ground truth (ADR-0007)."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

import yaml

from stream.schema import IncidentEvent

INCIDENTS_DIR = Path(__file__).parent / "incidents"
GROUND_TRUTH = INCIDENTS_DIR / "ground_truth.yaml"


@dataclass(frozen=True)
class EvalCase:
    id: str
    incident: IncidentEvent
    truth: dict


def load_cases() -> list[EvalCase]:
    entries = yaml.safe_load(GROUND_TRUTH.read_text(encoding="utf-8"))
    cases = []
    for e in entries:
        fixture = e["fixture"]
        incident = IncidentEvent.model_validate_json((INCIDENTS_DIR / fixture).read_text(encoding="utf-8"))
        cases.append(EvalCase(id=Path(fixture).stem, incident=incident, truth=e["truth"]))
    return cases
