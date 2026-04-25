from app.modules.gate.base import GateController
from app.modules.gate.home_assistant import HomeAssistantGateController
from app.modules.lpr.base import LprAdapter
from app.modules.lpr.ubiquiti import UbiquitiLprAdapter
from app.modules.notifications.apprise_client import AppriseNotificationSender
from app.modules.notifications.base import NotificationSender


class UnsupportedModuleError(ValueError):
    """Raised when configuration requests an unknown integration module."""


def get_lpr_adapter(name: str) -> LprAdapter:
    """Return a configured LPR adapter by plugin name."""

    adapters: dict[str, LprAdapter] = {
        "ubiquiti": UbiquitiLprAdapter(),
    }
    try:
        return adapters[name]
    except KeyError as exc:
        raise UnsupportedModuleError(f"Unsupported LPR adapter: {name}") from exc


def get_gate_controller(name: str) -> GateController:
    """Return a configured gate controller by plugin name."""

    controllers: dict[str, GateController] = {
        "home_assistant": HomeAssistantGateController(),
    }
    try:
        return controllers[name]
    except KeyError as exc:
        raise UnsupportedModuleError(f"Unsupported gate controller: {name}") from exc


def get_notification_sender(name: str) -> NotificationSender:
    """Return a configured notification sender by plugin name."""

    senders: dict[str, NotificationSender] = {
        "apprise": AppriseNotificationSender(),
    }
    try:
        return senders[name]
    except KeyError as exc:
        raise UnsupportedModuleError(f"Unsupported notification sender: {name}") from exc
