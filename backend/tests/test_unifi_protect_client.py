from app.modules.unifi_protect.client import close_unifi_protect_client


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
