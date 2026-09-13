"""Canonical schedule validation and adapter ownership boundaries."""
import ast
from pathlib import Path

import pytest

from app.services.schedule_operations import ScheduleOperationError, validate_schedule_values


def test_schedule_values_normalize_text_and_merge_intervals():
    values = validate_schedule_values({'name': '  Synthetic  ', 'description': '  ', 'time_blocks': {
        '0': [{'start': '08:00', 'end': '09:00'}, {'start': '09:00', 'end': '10:00'}],
    }})
    assert values.name == 'Synthetic'
    assert values.description is None
    assert values.time_blocks == {'0': [{'start': '08:00', 'end': '10:00'}], **{str(i): [] for i in range(1, 7)}}


@pytest.mark.parametrize('changes', [
    {'name': '   '}, {'name': 'x' * 121},
    {'time_blocks': {'0': [{'start': '08:15', 'end': '09:00'}]}},
    {'time_blocks': {'0': [{'start': '09:00', 'end': '08:00'}]}},
    {'time_blocks': {'0': [None]}},
])
def test_invalid_values_raise_domain_errors(changes):
    with pytest.raises(ScheduleOperationError) as exc:
        validate_schedule_values({'name': 'Synthetic', **changes})
    assert exc.value.code == 'invalid_schedule'


@pytest.mark.parametrize('relative', ['api/v1/schedules.py', 'ai/tool_groups/schedules_handlers.py'])
def test_crud_adapters_delegate_persistence_to_schedule_operations(relative):
    app = Path(__file__).resolve().parents[1] / 'app'
    tree = ast.parse((app / relative).read_text())
    for function in tree.body:
        if isinstance(function, ast.AsyncFunctionDef) and function.name in {'create_schedule', 'update_schedule', 'delete_schedule'}:
            calls = [n.func for n in ast.walk(function) if isinstance(n, ast.Call)]
            assert any(isinstance(call, ast.Attribute) and isinstance(call.value, ast.Name)
                       and call.value.id == 'schedule_operations' and call.attr == function.name for call in calls)
            assert not any(isinstance(call, ast.Attribute) and call.attr in {'commit', 'add', 'delete'}
                           and isinstance(call.value, ast.Name) and call.value.id == 'session' for call in calls)
