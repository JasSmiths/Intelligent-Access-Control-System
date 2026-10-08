"""Actual locked SDK calls; no policy, database sessions or unowned native work."""
from __future__ import annotations

import base64
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

from pyicloud import PyiCloudService
from pyicloud.hsa2_bridge import TrustedDeviceBridgeBootstrapper

from app.core.config import settings
from app.modules.icloud_calendar.bridge import CalendarBridgeSocket
from app.modules.icloud_calendar.contracts import (
    CalendarCoverage,
    CalendarSnapshot,
    ICloudCalendarClientError,
    ICloudCalendarReauthRequired,
)
from app.modules.icloud_calendar.events import normalize_snapshot
from app.modules.icloud_calendar.native import CalendarNativeOwner
from app.modules.icloud_calendar.transport import CalendarHTTPAdapter

ICLOUD_RECONNECT_REQUIRED_MESSAGE = (
    "Stored iCloud session is no longer accepted by Apple. Reconnect this account."
)


@dataclass
class ICloudAuthSession:
    apple_id: str
    api: PyiCloudService
    cookie_directory: Path
    adapter: CalendarHTTPAdapter
    operation: CalendarNativeOwner | None
    bridges: list[CalendarBridgeSocket] = field(default_factory=list)


class ICloudCalendarClient:
    """Synchronous calls run only inside the service's one actual SDK child."""

    def start_auth(
        self, apple_id: str, password: str, *, operation: CalendarNativeOwner
    ) -> ICloudAuthSession:
        return self._service(apple_id, password=password, operation=operation)

    def activate(self, auth_session: ICloudAuthSession, operation: CalendarNativeOwner) -> None:
        operation.work.check()
        auth_session.operation = operation
        auth_session.adapter.work = operation.work
        for bridge in auth_session.bridges:
            if not bridge.closed:
                bridge.activate(operation)
        operation.work.check()

    def detach(self, auth_session: ICloudAuthSession) -> None:
        for bridge in auth_session.bridges:
            if not bridge.closed:
                bridge.detach()
        auth_session.operation = None

    def requires_security_key(self, auth_session: ICloudAuthSession) -> bool:
        return bool(auth_session.api.security_key_names)

    def requires_2fa(self, auth_session: ICloudAuthSession) -> bool:
        return bool(auth_session.api.requires_2fa)

    def requires_legacy_2sa(self, auth_session: ICloudAuthSession) -> bool:
        return bool(auth_session.api.requires_2sa and not auth_session.api.requires_2fa)

    def request_2fa_code(self, auth_session: ICloudAuthSession) -> None:
        self._current(auth_session).work.check()
        try:
            auth_session.api.request_2fa_code()
        except Exception as exc:  # noqa: BLE001 - Vendor failures are sanitized after checking the current operation.
            self._raise_provider_failure(auth_session, exc, "Unable to request an Apple verification code.")
        self._current(auth_session).work.check()

    def validate_2fa_code(self, auth_session: ICloudAuthSession, code: str) -> bool:
        self._current(auth_session).work.check()
        try:
            result = bool(auth_session.api.validate_2fa_code(code))
        except Exception as exc:  # noqa: BLE001 - Vendor failures are sanitized after checking the current operation.
            self._raise_provider_failure(auth_session, exc, "Unable to verify the Apple verification code.")
        self._current(auth_session).work.check()
        return result

    def trust_session(self, auth_session: ICloudAuthSession) -> bool:
        self._current(auth_session).work.check()
        try:
            result = bool(auth_session.api.is_trusted_session or auth_session.api.trust_session())
        except Exception as exc:  # noqa: BLE001 - Vendor failures are sanitized after checking the current operation.
            self._raise_provider_failure(auth_session, exc, "Unable to trust the verified Apple session.")
        self._current(auth_session).work.check()
        return result

    def session_bundle(self, auth_session: ICloudAuthSession) -> dict[str, Any]:
        self._current(auth_session).work.check()
        return _bundle_directory(auth_session.cookie_directory)

    def cleanup_auth_session(self, auth_session: ICloudAuthSession) -> None:
        """Close all acquired handles even when stop/deadline forbids new work."""
        failure: BaseException | None = None
        for bridge in auth_session.bridges:
            try:
                bridge.close()
            except BaseException as exc:  # noqa: BLE001 - Observe every acquired handle before propagating cleanup failure.
                failure = failure or exc
        try:
            # Clear the SDK's retained reference as well as our concrete handles.
            auth_session.api._clear_trusted_device_bridge_state()
        except BaseException as exc:  # noqa: BLE001 - Observe every acquired handle before propagating cleanup failure.
            failure = failure or exc
        try:
            auth_session.api.session.close()
        except BaseException as exc:  # noqa: BLE001 - Observe every acquired handle before propagating cleanup failure.
            failure = failure or exc
        try:
            shutil.rmtree(auth_session.cookie_directory)
        except FileNotFoundError:
            pass
        except BaseException as exc:  # noqa: BLE001 - Observe every acquired handle before propagating cleanup failure.
            failure = failure or exc
        auth_session.operation = None
        if failure is not None:
            raise failure

    def fetch_snapshot(
        self, *, apple_id: str, session_bundle: dict[str, Any],
        coverage: CalendarCoverage, operation: CalendarNativeOwner,
    ) -> CalendarSnapshot:
        auth_session = self._service(apple_id, password=None, operation=operation,
                                     session_bundle=session_bundle)
        try:
            if auth_session.api.requires_2fa or auth_session.api.requires_2sa:
                raise ICloudCalendarReauthRequired(
                    "iCloud requested verification again. Reconnect this account."
                )
            operation.work.check()
            calendar = auth_session.api.calendar
            auth_session.adapter.bind_coverage(f"{calendar.service_root}/ca/events", coverage)
            response = calendar.refresh_client(coverage.requested_start, coverage.requested_end)
            operation.work.check()
            return normalize_snapshot(response, coverage)
        except Exception as exc:  # noqa: BLE001 - Vendor failures are sanitized after checking the current operation.
            self._raise_provider_failure(auth_session, exc, "Unable to read iCloud Calendar. Try again or reconnect the account.")
        finally:
            self.cleanup_auth_session(auth_session)

    def _service(
        self, apple_id: str, *, password: str | None, operation: CalendarNativeOwner,
        session_bundle: dict[str, Any] | None = None,
    ) -> ICloudAuthSession:
        operation.work.check()
        cookie_directory = self._new_cookie_directory()
        auth_session: ICloudAuthSession | None = None
        try:
            if session_bundle is not None:
                _restore_directory_bundle(cookie_directory, session_bundle)
            # Constructor does no authentication. Install finite HTTP and bridge
            # ports before the first real authentication request is possible.
            api = PyiCloudService(apple_id, password, cookie_directory=str(cookie_directory),
                                 authenticate=False)
            adapter = CalendarHTTPAdapter(operation.work)
            api.session.mount("https://", adapter)
            api.session.mount("http://", adapter)
            auth_session = ICloudAuthSession(apple_id, api, cookie_directory, adapter, operation)

            def bridge_factory(url: str, timeout: float, origin: str, user_agent: str) -> CalendarBridgeSocket:
                current = self._current(auth_session)
                bridge = CalendarBridgeSocket(url, timeout, origin, user_agent, operation=current)
                auth_session.bridges.append(bridge)
                return bridge

            api._trusted_device_bridge = TrustedDeviceBridgeBootstrapper(websocket_factory=bridge_factory)
            operation.work.check()
            api.authenticate()
            operation.work.check()
            return auth_session
        except BaseException as exc:  # noqa: BLE001 - Observe every acquired handle before propagating cleanup failure.
            if auth_session is not None:
                try:
                    self.cleanup_auth_session(auth_session)
                except BaseException as cleanup_error:
                    raise cleanup_error from exc
            else:
                shutil.rmtree(cookie_directory)
            operation.work.check()
            if not isinstance(exc, Exception):
                raise
            if "no password set" in str(exc).lower():
                raise ICloudCalendarReauthRequired(ICLOUD_RECONNECT_REQUIRED_MESSAGE) from exc
            if isinstance(exc, ICloudCalendarClientError):
                raise
            raise ICloudCalendarClientError("Unable to connect to iCloud. Check the account and try again.") from exc

    @staticmethod
    def _current(auth_session: ICloudAuthSession) -> CalendarNativeOwner:
        if auth_session.operation is None:
            raise RuntimeError("Pending Apple authentication has no admitted SDK operation.")
        return auth_session.operation

    @classmethod
    def _raise_provider_failure(cls, auth_session: ICloudAuthSession, exc: Exception, detail: str) -> NoReturn:
        cls._current(auth_session).work.check()
        if isinstance(exc, ICloudCalendarClientError):
            raise exc
        raise ICloudCalendarClientError(detail) from exc

    @staticmethod
    def _new_cookie_directory() -> Path:
        root = settings.data_dir / "icloud-calendar-sessions"
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="session-", dir=root))


def _bundle_directory(directory: Path) -> dict[str, Any]:
    files: list[dict[str, str]] = []
    for path in directory.rglob("*"):
        if path.is_file():
            files.append({"path": path.relative_to(directory).as_posix(),
                          "content_b64": base64.b64encode(path.read_bytes()).decode("ascii")})
    return {"version": 1, "files": files}


def _restore_directory_bundle(directory: Path, bundle: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for item in bundle.get("files") or []:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("path") or "").strip()
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            continue
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(str(item.get("content_b64") or "")))
