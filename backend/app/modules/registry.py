from app.modules.gate.base import GateController
from app.modules.gate.access_devices import AccessDeviceGateController


class UnsupportedModuleError(ValueError):
    """Raised when configuration requests an unknown integration module."""


def get_gate_controller(name: str) -> GateController:
    """Return a configured gate controller by plugin name."""

    controllers: dict[str, GateController] = {
        "configured": AccessDeviceGateController(),
        "access_device": AccessDeviceGateController(),
    }
    try:
        return controllers[name]
    except KeyError as exc:
        raise UnsupportedModuleError(f"Unsupported gate controller: {name}") from exc
