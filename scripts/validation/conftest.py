"""Bind application ports for isolated fixture tests without starting services."""

import pytest

from app.composition import wire_application


@pytest.fixture(autouse=True)
def wire_inert_application_effects():
    wire_application()
