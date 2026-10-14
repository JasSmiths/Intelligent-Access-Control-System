from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.notifications.base import NotificationDeliveryError
from app.services import notifications
from app.services.notification_dispatch import _checkpoint_from_delivery_error, _checkpoint_from_outcome


@pytest.mark.parametrize("deliveries", [
    ["accepted", "rejected"], ["accepted", "unknown"], ["not_sent", "accepted"],
    ["accepted", "accepted"], ["not_sent", "not_sent"], ["rejected", "unknown"],
])
async def test_voice_destination_truth_survives_later_failure(monkeypatch, deliveries):
    targets = [f"media_player.synthetic_{i}" for i in range(len(deliveries))]
    class Announcer:
        async def announce(self, target, _message, **_):
            delivery = deliveries[targets.index(target.entity_id)]
            if delivery != "accepted":
                raise NotificationDeliveryError("synthetic provider outcome", delivery=delivery)
    monkeypatch.setattr(notifications, "HomeAssistantTtsAnnouncer", Announcer)
    service = notifications.NotificationService()
    monkeypatch.setattr(service, "_voice_announcements_preflight", AsyncMock(return_value=None))
    try:
        outcome = await service._send_voice({"message": "Synthetic", "frozen_voice_targets": targets}, SimpleNamespace())
        checkpoint = _checkpoint_from_outcome(outcome)
    except NotificationDeliveryError as error:
        checkpoint = _checkpoint_from_delivery_error(error)
    assert checkpoint["accepted_any"] == ("accepted" in deliveries)
    assert checkpoint["review_required"] == ("unknown" in deliveries)
    assert checkpoint["destination_outcomes"] == [
        {"target": target, "delivery": delivery} for target, delivery in zip(targets, deliveries)]
