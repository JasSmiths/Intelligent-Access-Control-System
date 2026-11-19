"""Keep the IACS camera and LPR fields that Protect supplies outside the SDK models."""

import logging
from typing import Any

from pydantic import PrivateAttr, ValidationError
from uiprotect import ProtectApiClient
from uiprotect.data import Bootstrap, Camera, Event, ModelType
from uiprotect.data.convert import create_from_unifi_dict
from uiprotect.data.websocket import WSAction, WSPacket, WSSubscriptionMessage
from uiprotect.exceptions import ClientError, DataDecodeError, NvrError

from .camera_catalog import CameraWireCatalog, CameraWireMetadata
from .event_evidence import apply_event_thumbnails, capture_event_thumbnails

logger = logging.getLogger(__name__)


def event_from_wire(api: ProtectApiClient, raw: dict[str, Any]) -> Event:
    evidence = capture_event_thumbnails(raw["id"], raw)
    event = Event.from_unifi_dict(**{**raw, "modelKey": "event"}, api=api)
    apply_event_thumbnails(event, evidence)
    return event


class IacsProtectBootstrap(Bootstrap):
    _camera_catalog: CameraWireCatalog = PrivateAttr(default_factory=CameraWireCatalog)
    _camera_refreshes: dict[str, object] = PrivateAttr(default_factory=dict)

    @property
    def camera_catalog(self) -> CameraWireCatalog:
        return self._camera_catalog

    @classmethod
    def from_response(cls, api: ProtectApiClient, raw: dict[str, Any]) -> "IacsProtectBootstrap":
        # The SDK consumes and normalizes nested dictionaries in place.
        catalog = CameraWireCatalog.from_bootstrap(raw.get("cameras", []))
        bootstrap = cls.from_unifi_dict(api=api, **raw)
        catalog.retain(bootstrap.cameras)
        bootstrap._camera_catalog = catalog
        return bootstrap

    async def refresh_device(self, model_type: ModelType, device_id: str) -> None:
        if model_type is not ModelType.CAMERA:
            await super().refresh_device(model_type, device_id)
            return

        token = object()
        self._camera_refreshes[device_id] = token
        try:
            if self._api.bootstrap is not self:
                return
            raw = await self._api.get_device_raw(model_type, device_id)
            if self._api.bootstrap is not self or self._camera_refreshes.get(device_id) is not token:
                return
            if raw.get("id") != device_id:
                raise DataDecodeError("Unexpected camera identity in refresh response")
            # Mirror get_device's SDK conversion and adoption check after detaching
            # fields the conversion discards. Both projections share the one read.
            metadata = CameraWireMetadata.from_raw(raw)
            camera = create_from_unifi_dict(raw, api=self._api)
            if not isinstance(camera, Camera):
                raise DataDecodeError("Unexpected camera in refresh response")
            if self._api.ignore_unadopted and not camera.is_adopted:
                raise NvrError("Device is not adopted")
            self.cameras[device_id] = camera
            self._camera_catalog.apply("add", device_id, metadata)
        except (ClientError, DataDecodeError, ValidationError):
            logger.warning("Failed to refresh UniFi camera")
        finally:
            if self._camera_refreshes.get(device_id) is token:
                self._camera_refreshes.pop(device_id)

    def process_ws_packet(
        self,
        packet: WSPacket,
        models: set[ModelType] | None = None,
        ignore_stats: bool = False,
        is_ping_back: bool = False,
    ) -> WSSubscriptionMessage | None:
        action, raw = packet.action_frame.data, packet.data_frame.data
        model, verb = action.get("modelKey"), action.get("action")
        identity = action.get("id") or raw.get("id")
        camera_delta = None
        thumbnails = None
        selected = not models or model in models
        if selected and isinstance(identity, str) and raw.get("id", identity) == identity:
            if model == "camera" and verb in ("add", "update", "remove"):
                camera_delta = CameraWireMetadata.from_raw(raw)
            elif model == "event" and verb in ("add", "update"):
                thumbnails = capture_event_thumbnails(identity, raw)

        message = super().process_ws_packet(packet, models, ignore_stats, is_ping_back)
        if thumbnails is not None and message is not None and isinstance(message.new_obj, Event):
            apply_event_thumbnails(message.new_obj, thumbnails, changed_data=message.changed_data)
        if camera_delta is not None:
            return self._apply_camera_delta(identity, verb, camera_delta, message, filtered=not raw)
        return message

    def _apply_camera_delta(
        self, identity: str, verb: str, delta: CameraWireMetadata,
        message: WSSubscriptionMessage | None, *, filtered: bool,
    ) -> WSSubscriptionMessage | None:
        if verb == "remove":
            if message is not None:
                self._camera_refreshes.pop(identity, None)
                self._camera_catalog.apply(verb, identity, delta)
            return message
        camera = self.cameras.get(identity)
        if camera is None:
            return message
        changes = delta.camera_changes()
        if message is None and verb == "update" and filtered and changes:
            # All SDK fields were filtered during normalization. A selected
            # private zone/motion change still belongs to this adopted camera.
            message = WSSubscriptionMessage(
                action=WSAction.UPDATE, new_update_id=self.last_update_id,
                changed_data={"modelKey": "camera", "id": identity}, new_obj=camera,
            )
        if message is not None and message.new_obj is camera:
            self._camera_refreshes.pop(identity, None)
            self._camera_catalog.apply(verb, identity, delta)
            message.changed_data.update(changes)
        return message


class IacsProtectClient(ProtectApiClient):
    async def get_bootstrap(self) -> IacsProtectBootstrap:
        # This client is only constructed for IACS hybrid credentials. Use the
        # SDK's authenticated read and normal bootstrap parse/adoption filters.
        return IacsProtectBootstrap.from_response(self, await self.api_request_obj("bootstrap"))

    async def get_event(self, event_id: str) -> Event:
        raw = await self.get_device_raw(ModelType.EVENT, event_id)
        if raw.get("id") != event_id or raw.get("modelKey", "event") != "event":
            raise NvrError("Unexpected event identity in Protect response")
        return event_from_wire(self, raw)
