def test_apprise_occurrence_receipts_are_safe_and_urls_remain_redacted():
    from app.services.notification_runs import safe_destination_outcomes
    assert safe_destination_outcomes([
        {"target": "apprise:1", "delivery": "accepted"},
        {"target": "apprise://synthetic-credential", "delivery": "unknown"},
    ]) == [{"target": "apprise:1", "delivery": "accepted"}]


import pytest
from pydantic import ValidationError
from app.services import integration_settings
from app.services.notification_recipient_binding import recipient_binding_error
from app.services.workflows.channel_definitions import NotificationConfiguration


@pytest.mark.parametrize("targets", [[], ["apprise:*"], ["apprise:999"], ["apprise:" + "9" * 5000], [None]])
def test_invalid_selected_targets_fail_closed_without_conversion_errors(monkeypatch, targets):
    monkeypatch.setattr(integration_settings, "get_auth_secret", lambda: "synthetic-recipient-root")
    assert recipient_binding_error({"type": "mobile", "target_mode": "selected", "target_ids": targets}, "json://synthetic.invalid/a") == "recipient_selection_invalid"


def test_root_rotation_stales_existing_binding_without_rebinding(monkeypatch):
    root = ["synthetic-recipient-root-before"]
    monkeypatch.setattr(integration_settings, "get_auth_secret", lambda: root[0])
    configured = "json://synthetic.invalid/a"
    action = {"type": "mobile", "target_mode": "selected", "target_ids": ["apprise:0"],
        "apprise_revision": integration_settings.apprise_revision(configured)}
    assert recipient_binding_error(action, configured) is None
    root[0] = "synthetic-recipient-root-after"
    assert recipient_binding_error(action, configured) == "recipient_binding_stale"


@pytest.mark.parametrize("value", [True, 12, "not-a-fingerprint", "é" * 64])
def test_recipient_contract_rejects_malformed_binding(value):
    with pytest.raises(ValidationError):
        NotificationConfiguration.model_validate({"apprise_revision": value})
