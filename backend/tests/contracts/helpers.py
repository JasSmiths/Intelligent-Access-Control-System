import json
from pathlib import Path
from typing import Any


FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"


def load_contract_fixture(relative_path: str) -> Any:
    with (FIXTURES_ROOT / relative_path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def assert_contract_subset(actual: Any, expected: Any) -> None:
    """Assert that a golden contract fixture is present in a richer payload."""

    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        for key, expected_value in expected.items():
            assert key in actual
            assert_contract_subset(actual[key], expected_value)
        return
    if isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) == len(expected)
        for actual_item, expected_item in zip(actual, expected, strict=True):
            assert_contract_subset(actual_item, expected_item)
        return
    assert actual == expected

