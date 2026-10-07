"""Stage ownership and pure policy boundaries for the access pipeline."""
import ast
from itertools import product
from pathlib import Path

import pytest

from app.models.enums import AccessDecision, AccessDirection
from app.services.access.decision import access_is_allowed, plan_access

ROOT = Path(__file__).resolve().parents[1] / 'app' / 'services'


def imported_modules(filename):
    tree = ast.parse(filename.read_text())
    return {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}


def test_decision_is_pure_and_stages_do_not_depend_on_orchestrator():
    assert imported_modules(ROOT / 'access/decision.py') <= {
        'dataclasses', 'app.models.enums', 'app.modules.gate.base', '__future__',
    }
    for name in ('reads', 'evidence', 'execution', 'enrichment', 'payloads'):
        modules = imported_modules(ROOT / f'access/{name}.py')
        assert 'app.services.access_events' not in modules
        tree = ast.parse((ROOT / f'access/{name}.py').read_text())
        assert not any(isinstance(n, ast.ImportFrom) and any(a.name == '*' for a in n.names) for n in ast.walk(tree))
    orchestrator = imported_modules(ROOT / 'access_events.py')
    assert not orchestrator.intersection({'app.services.dvla', 'app.services.unifi_protect',
        'app.services.access.hardware', 'app.services.vehicle_visual_detections'})
    evidence = imported_modules(ROOT / 'access/evidence.py')
    assert not evidence.intersection({'app.services.access.hardware', 'app.services.access.execution', 'app.services.access.enrichment'})
    execution = imported_modules(ROOT / 'access/execution.py')
    assert not execution.intersection({'app.services.dvla', 'app.services.unifi_protect', 'app.services.access.enrichment'})
    enrichment = imported_modules(ROOT / 'access/enrichment.py')
    assert not enrichment.intersection({'app.services.gate_commands', 'app.services.access.hardware', 'app.services.access_devices'})


@pytest.mark.parametrize('schedule,identity,visitor,external', list(product([False, True], repeat=4)))
def test_identity_and_schedule_policy(schedule, identity, visitor, external):
    assert access_is_allowed(schedule_allowed=schedule, identity_active=identity,
        visitor_pass_matched=visitor, external_admission_matched=external) == (schedule and (identity or visitor or external))


@pytest.mark.parametrize('direction', list(AccessDirection))
@pytest.mark.parametrize('allowed', [False, True])
def test_access_plan_exposes_decision_and_resolved_direction(allowed, direction):
    plan = plan_access(allowed=allowed, direction=direction)
    assert plan.allowed is allowed
    assert plan.direction == direction
    assert plan.decision == (AccessDecision.GRANTED if allowed else AccessDecision.DENIED)
