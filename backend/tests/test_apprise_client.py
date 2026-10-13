"""Apprise 2 result semantics at the delivery boundary, without provider I/O."""

from unittest.mock import Mock

import apprise
import pytest

from app.modules.notifications.apprise_client import AppriseNotificationSender
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError


@pytest.mark.asyncio
@pytest.mark.parametrize("status", list(apprise.AppriseResultStatus))
@pytest.mark.parametrize("attachments", [None, ["/synthetic/photo.jpg"]])
async def test_only_successful_apprise_result_accepts_delivery(monkeypatch, status, attachments):
    # URL parsing is real; only the outbound notification call is replaced.
    notify = Mock(return_value=apprise.AppriseResult(status=status))
    monkeypatch.setattr(apprise.Apprise, "notify", notify)
    sender = AppriseNotificationSender("json://example.invalid/notify")
    context = NotificationContext("test", "Synthetic", "info", {})

    if status == apprise.AppriseResultStatus.SUCCESS:
        await sender.send("Title", "Body", context, attachments=attachments)
    else:
        with pytest.raises(NotificationDeliveryError, match="did not deliver"):
            await sender.send("Title", "Body", context, attachments=attachments)

    expected = {"title": "Title", "body": "Body"}
    if attachments:
        expected["attach"] = attachments
    notify.assert_called_once_with(**expected)
