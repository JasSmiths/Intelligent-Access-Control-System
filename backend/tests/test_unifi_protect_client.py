from types import SimpleNamespace

from app.modules.unifi_protect.client import (
    close_unifi_protect_client,
    is_unifi_protect_stream_metadata_only,
    subscribe_unifi_protect,
)


class FakeClosable:
    def __init__(self, calls, label: str, *, async_close: bool = False) -> None:
        self.calls = calls
        self.label = label
        self.async_close = async_close

    async def _async_close(self) -> None:
        self.calls.append(self.label)

    def close(self):
        if self.async_close:
            return self._async_close()
        self.calls.append(self.label)
        return None


class FakeProtectApi:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.session = FakeClosable(self.calls, "session.close")
        self._public_api_session = FakeClosable(self.calls, "public_api_session.close", async_close=True)
        self.websocket = FakeClosable(self.calls, "websocket.close")

    async def async_disconnect_ws(self) -> None:
        self.calls.append("api.async_disconnect_ws")

    def disconnect_ws(self) -> None:
        self.calls.append("api.disconnect_ws")

    def close_session(self) -> None:
        self.calls.append("api.close_session")

    async def close_public_api_session(self) -> None:
        self.calls.append("api.close_public_api_session")

    def close(self) -> None:
        self.calls.append("api.close")


async def test_close_unifi_protect_client_closes_api_and_underlying_sessions() -> None:
    api = FakeProtectApi()

    await close_unifi_protect_client(api)

    assert api.calls == [
        "api.async_disconnect_ws",
        "api.disconnect_ws",
        "api.close_session",
        "api.close_public_api_session",
        "api.close",
        "session.close",
        "public_api_session.close",
        "websocket.close",
    ]


class FakeSubscriptionApi:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.state_callbacks = {}

    def _state(self, channel: str, callback):
        self.calls.append(f"state:{channel}")
        self.state_callbacks[channel] = callback
        return lambda: None

    def _message(self, channel: str, callback):
        self.calls.append(f"message:{channel}")
        return lambda: None

    def subscribe_websocket_state(self, callback):
        return self._state("private", callback)

    def subscribe_events_websocket_state(self, callback):
        return self._state("events", callback)

    def subscribe_devices_websocket_state(self, callback):
        return self._state("devices", callback)

    def subscribe_websocket(self, callback):
        return self._message("private", callback)

    def subscribe_events_websocket(self, callback):
        return self._message("events", callback)

    def subscribe_devices_websocket(self, callback):
        return self._message("devices", callback)


def test_subscribe_unifi_protect_registers_channel_states_before_starting_sockets() -> None:
    api = FakeSubscriptionApi()
    states = []

    unsubscribers = subscribe_unifi_protect(api, lambda _: None, lambda channel, state: states.append((channel, state)))

    assert len(unsubscribers) == 6
    assert api.calls == [
        "state:private",
        "state:events",
        "state:devices",
        "message:private",
        "message:events",
        "message:devices",
    ]
    for channel in ("private", "events", "devices"):
        api.state_callbacks[channel](channel.upper())
    assert states == [("private", "PRIVATE"), ("events", "EVENTS"), ("devices", "DEVICES")]


def test_stream_metadata_only_message_is_identified_without_hiding_detection_changes() -> None:
    assert is_unifi_protect_stream_metadata_only(
        SimpleNamespace(changed_data={"modelKey": "camera", "id": "cam-1", "rtsps_streams": {}})
    )
    assert not is_unifi_protect_stream_metadata_only(
        SimpleNamespace(
            changed_data={
                "modelKey": "camera",
                "id": "cam-1",
                "rtsps_streams": {},
                "isVehicleCurrentlyDetected": True,
            }
        )
    )
    assert not is_unifi_protect_stream_metadata_only(SimpleNamespace(changed_data={"modelKey": "event", "id": "event-1"}))
