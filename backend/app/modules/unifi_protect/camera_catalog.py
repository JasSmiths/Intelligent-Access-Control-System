"""Selected private camera fields that Protect's typed SDK models omit."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class CameraSmartZone:
    id: int | str
    name: str
    object_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CameraChannelStatus:
    name: str | None
    is_rtsp_enabled: bool | None
    is_package: bool | None


def _wire_id(value: object) -> int | str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value:
        return value
    return None


def _read_zones(value: object) -> tuple[CameraSmartZone, ...] | None:
    if not isinstance(value, list):
        return None
    zones: list[CameraSmartZone] = []
    for item in value:
        if not isinstance(item, dict) or (zone_id := _wire_id(item.get("id"))) is None:
            return None
        name = item.get("name")
        types = item.get("objectTypes", [])
        if not isinstance(name, str) or not isinstance(types, list):
            return None
        if not all(isinstance(kind, str) for kind in types):
            return None
        zones.append(CameraSmartZone(zone_id, name, tuple(types)))
    return tuple(zones)


def _read_channels(value: object) -> Mapping[str, CameraChannelStatus] | None:
    if not isinstance(value, list):
        return None
    channels: dict[str, CameraChannelStatus] = {}
    for item in value:
        if not isinstance(item, dict) or (channel_id := _wire_id(item.get("id"))) is None:
            return None
        key = str(channel_id)
        if key in channels:
            return None
        name = item.get("name")
        enabled = item.get("isRtspEnabled")
        package = item.get("isPackage")
        channels[key] = CameraChannelStatus(
            name if isinstance(name, str) else None,
            enabled if isinstance(enabled, bool) else None,
            package if isinstance(package, bool) else None,
        )
    return MappingProxyType(channels)


_CAMERA_FIELDS = frozenset(("smartDetectZones", "channels", "isMotionDetected"))


@dataclass(frozen=True, slots=True)
class CameraWireMetadata:
    zones: tuple[CameraSmartZone, ...] | None
    channels: Mapping[str, CameraChannelStatus] | None
    is_motion_detected: bool | None
    has_package_camera: bool | None
    _supplied: frozenset[str] = field(repr=False)

    def zone_rows(self) -> list[dict[str, object]]:
        return [
            {"id": zone.id, "name": zone.name, "object_types": list(zone.object_types)}
            for zone in self.zones or ()
        ]

    def camera_changes(self) -> dict[str, object]:
        changes: dict[str, object] = {}
        if "isMotionDetected" in self._supplied:
            changes["is_motion_detected"] = self.is_motion_detected
        if "smartDetectZones" in self._supplied:
            changes["smart_detect_zones"] = self.zone_rows()
        return changes

    @classmethod
    def from_raw(cls, camera: Mapping[str, object]) -> "CameraWireMetadata":
        """Detach selected fields before SDK normalization mutates the source."""
        motion = camera.get("isMotionDetected")
        fields = _CAMERA_FIELDS.intersection(camera)
        flags = camera.get("featureFlags")
        package = None
        if isinstance(flags, dict) and "hasPackageCamera" in flags:
            package = flags["hasPackageCamera"]
            fields = fields.union(("hasPackageCamera",))
        return cls(
            _read_zones(camera.get("smartDetectZones")),
            _read_channels(camera.get("channels")),
            motion if isinstance(motion, bool) else None,
            package if isinstance(package, bool) else None,
            fields,
        )

    def with_update(self, update: "CameraWireMetadata") -> "CameraWireMetadata":
        fields = update._supplied
        if not fields:
            return self
        return CameraWireMetadata(
            update.zones if "smartDetectZones" in fields else self.zones,
            update.channels if "channels" in fields else self.channels,
            update.is_motion_detected if "isMotionDetected" in fields else self.is_motion_detected,
            update.has_package_camera if "hasPackageCamera" in fields else self.has_package_camera,
            self._supplied | fields,
        )


class CameraWireCatalog:
    """One synchronous, camera-ID-indexed catalog per private bootstrap."""

    def __init__(self) -> None:
        self._cameras: dict[str, CameraWireMetadata] = {}

    @classmethod
    def from_bootstrap(cls, raw_cameras: object) -> "CameraWireCatalog":
        catalog = cls()
        if isinstance(raw_cameras, list):
            for camera in raw_cameras:
                if isinstance(camera, dict):
                    camera_id = camera.get("id")
                    if isinstance(camera_id, str) and camera_id:
                        catalog._cameras[camera_id] = CameraWireMetadata.from_raw(camera)
        return catalog

    def retain(self, camera_ids: Iterable[str]) -> None:
        """Keep cameras admitted by the SDK after its bootstrap adoption checks."""
        for camera_id in self._cameras.keys() - set(camera_ids):
            del self._cameras[camera_id]

    def get(self, camera_id: str) -> CameraWireMetadata | None:
        return self._cameras.get(camera_id)

    def apply(self, action: str, camera_id: str, captured: CameraWireMetadata) -> None:
        if action == "remove":
            self._cameras.pop(camera_id, None)
        elif action == "add":
            self._cameras[camera_id] = captured
        elif action == "update" and (current := self._cameras.get(camera_id)) is not None:
            self._cameras[camera_id] = current.with_update(captured)
