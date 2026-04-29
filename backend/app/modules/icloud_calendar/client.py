import base64
import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings


class ICloudCalendarClientError(RuntimeError):
    """Raised when iCloud calendar authentication or sync fails."""


class ICloudCalendarReauthRequired(ICloudCalendarClientError):
    """Raised when a stored iCloud session can no longer be used."""


@dataclass(frozen=True)
class ICloudCalendarEvent:
    calendar_id: str
    event_id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    description: str
    notes: str
    raw: dict[str, Any]


@dataclass
class ICloudAuthSession:
    apple_id: str
    api: Any
    cookie_directory: Path


class ICloudCalendarClient:
    """Small adapter around pyicloud so the rest of IACS never imports vendor APIs."""

    def start_auth(self, apple_id: str, password: str) -> ICloudAuthSession:
        cookie_directory = self._new_cookie_directory()
        api = self._service(apple_id, password=password, cookie_directory=cookie_directory)
        return ICloudAuthSession(apple_id=apple_id, api=api, cookie_directory=cookie_directory)

    def requires_security_key(self, auth_session: ICloudAuthSession) -> bool:
        try:
            return bool(getattr(auth_session.api, "security_key_names", None))
        except Exception:
            return False

    def requires_2fa(self, auth_session: ICloudAuthSession) -> bool:
        return bool(getattr(auth_session.api, "requires_2fa", False))

    def requires_2sa(self, auth_session: ICloudAuthSession) -> bool:
        return bool(getattr(auth_session.api, "requires_2sa", False))

    def requires_legacy_2sa(self, auth_session: ICloudAuthSession) -> bool:
        return self.requires_2sa(auth_session) and not self.requires_2fa(auth_session)

    def request_2fa_code(self, auth_session: ICloudAuthSession) -> None:
        requester = getattr(auth_session.api, "request_2fa_code", None)
        if callable(requester):
            requester()

    def validate_2fa_code(self, auth_session: ICloudAuthSession, code: str) -> bool:
        validator = getattr(auth_session.api, "validate_2fa_code", None)
        if not callable(validator):
            raise ICloudCalendarClientError("This iCloud session cannot validate a verification code.")
        return bool(validator(code))

    def trust_session(self, auth_session: ICloudAuthSession) -> bool:
        try:
            if bool(getattr(auth_session.api, "is_trusted_session", False)):
                return True
        except Exception:
            return False
        trust = getattr(auth_session.api, "trust_session", None)
        if not callable(trust):
            return False
        return bool(trust())

    def session_bundle(self, auth_session: ICloudAuthSession) -> dict[str, Any]:
        return _bundle_directory(auth_session.cookie_directory)

    def cleanup_auth_session(self, auth_session: ICloudAuthSession) -> None:
        shutil.rmtree(auth_session.cookie_directory, ignore_errors=True)

    def fetch_events(
        self,
        *,
        apple_id: str,
        session_bundle: dict[str, Any],
        starts_at: datetime,
        ends_at: datetime,
    ) -> list[ICloudCalendarEvent]:
        cookie_directory = self._new_cookie_directory()
        try:
            _restore_directory_bundle(cookie_directory, session_bundle)
            api = self._service(apple_id, password=None, cookie_directory=cookie_directory)
            if bool(getattr(api, "requires_2fa", False)) or bool(getattr(api, "requires_2sa", False)):
                raise ICloudCalendarReauthRequired("iCloud requested verification again. Reconnect this account.")
            raw_events = _fetch_raw_calendar_events(api, starts_at, ends_at)
            return [event for event in (_normalize_event(item) for item in raw_events) if event]
        finally:
            shutil.rmtree(cookie_directory, ignore_errors=True)

    def _fetch_calendar_events(self, api: Any, starts_at: datetime, ends_at: datetime) -> list[Any]:
        return _fetch_raw_calendar_events(api, starts_at, ends_at)

    def _service(self, apple_id: str, *, password: str | None, cookie_directory: Path) -> Any:
        try:
            from pyicloud import PyiCloudService
        except ImportError as exc:
            raise ICloudCalendarClientError(
                "pyicloud is not installed in the backend environment. Rebuild the backend container."
            ) from exc

        try:
            return PyiCloudService(
                apple_id,
                password,
                cookie_directory=str(cookie_directory),
            )
        except Exception as exc:
            raise ICloudCalendarClientError(str(exc) or "Unable to connect to iCloud.") from exc

    def _new_cookie_directory(self) -> Path:
        root = settings.data_dir / "icloud-calendar-sessions"
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="session-", dir=root))


def _fetch_raw_calendar_events(api: Any, starts_at: datetime, ends_at: datetime) -> list[Any]:
    calendar = getattr(api, "calendar", None)
    if calendar is None:
        raise ICloudCalendarClientError("iCloud Calendar service was not available for this account.")

    refresh_client = getattr(calendar, "refresh_client", None)
    if callable(refresh_client):
        response = refresh_client(starts_at, ends_at) or {}
        if isinstance(response, dict):
            return list(response.get("Event") or [])

    get_events = getattr(calendar, "get_events", None)
    if callable(get_events):
        try:
            return list(get_events(from_dt=starts_at, to_dt=ends_at, as_objs=False) or [])
        except TypeError:
            return list(get_events(from_dt=starts_at, period="2weeks", as_objs=False) or [])

    if hasattr(calendar, "events"):
        return list(calendar.events(starts_at, ends_at) or [])

    raise ICloudCalendarClientError("The installed pyicloud calendar client cannot fetch events.")


def _bundle_directory(directory: Path) -> dict[str, Any]:
    files: list[dict[str, str]] = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        files.append(
            {
                "path": relative,
                "content_b64": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        )
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


def _normalize_event(value: Any) -> ICloudCalendarEvent | None:
    title = _text(_event_value(value, "title", "summary", "name"))
    calendar_id = _text(_event_value(value, "pguid", "pGuid", "calendar_id", "calendarId", "calendarGuid"))
    event_id = _text(_event_value(value, "guid", "id", "event_id", "eventId"))
    timezone = _event_timezone(value)
    starts_at = _datetime_value(
        _event_value(value, "startDate", "start_date", "localStartDate", "local_start_date", "start", "starts_at"),
        timezone=timezone,
    )
    ends_at = _datetime_value(
        _event_value(value, "endDate", "end_date", "localEndDate", "local_end_date", "end", "ends_at"),
        timezone=timezone,
    )
    if not title or not event_id or not starts_at or not ends_at or ends_at <= starts_at:
        return None
    if bool(_event_value(value, "allDay", "all_day", "is_all_day")):
        return None
    return ICloudCalendarEvent(
        calendar_id=calendar_id or "calendar",
        event_id=event_id,
        title=title,
        starts_at=starts_at,
        ends_at=ends_at,
        description=_text(_event_value(value, "description", "desc")),
        notes=_text(_event_value(value, "notes", "note", "privateComments", "private_comments", "comments")),
        raw=_safe_event_raw(value),
    )


def _event_value(value: Any, *names: str) -> Any:
    if isinstance(value, dict):
        for name in names:
            if name in value:
                return value[name]
    getter = getattr(value, "get", None)
    if callable(getter):
        for name in names:
            try:
                result = getter(name)
            except TypeError:
                result = None
            if result is not None:
                return result
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _datetime_value(value: Any, *, timezone: ZoneInfo | None = None) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_aware(value, timezone=timezone)
    if isinstance(value, list | tuple) and len(value) >= 6:
        try:
            parsed = datetime(
                int(value[1]),
                int(value[2]),
                int(value[3]),
                int(value[4]),
                int(value[5]),
                tzinfo=timezone or UTC,
            )
        except (TypeError, ValueError):
            return None
        return parsed
    if isinstance(value, int | float):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            return _ensure_aware(datetime.fromisoformat(text), timezone=timezone)
        except ValueError:
            return None
    return None


def _ensure_aware(value: datetime, *, timezone: ZoneInfo | None = None) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone or UTC)
    return value.astimezone(UTC)


def _event_timezone(value: Any) -> ZoneInfo | None:
    for candidate in (
        _text(_event_value(value, "tz", "timezone", "timeZone", "time_zone")),
        _text(_event_value(value, "tzname", "time_zone_name")),
    ):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except Exception:
            continue
    return None


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _safe_event_raw(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        raw = value
    else:
        raw = {
            key: getattr(value, key)
            for key in ("guid", "pguid", "title", "start_date", "end_date")
            if hasattr(value, key)
        }
    try:
        json.dumps(raw, default=str)
    except TypeError:
        return {}
    return json.loads(json.dumps(raw, default=str)) if isinstance(raw, dict) else {}
