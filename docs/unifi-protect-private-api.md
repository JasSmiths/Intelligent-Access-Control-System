# UniFi Protect Private API Notes

Last checked: 2026-07-14.

This document captures the `uiprotect` private API behavior that IACS depends on.
It is intended as a recovery note if a future `uiprotect` release removes a
wrapper we still need.

The target is to recreate the IACS UniFi Protect read/diagnostic contract, not
the full `uiprotect` library. Do not rebuild unused private features such as
Protect device setting mutation, talkback, adoption, doorlocks, lights, or
viewers unless IACS starts depending on them.

## Current Runtime

The repository pins `uiprotect==15.12.2` in `backend/pyproject.toml`. The
running backend also uses the managed overlay mechanism, with the active
overlay kept at the same version so a container rebuild cannot silently
downgrade the live integration.

Current running backend state:

```text
current_unifi_protect_version=15.12.2
active_state.mode=overlay
active_state.version=15.12.2
uiprotect_file=/app/data/unifi-protect-package/versions/15.12.2/uiprotect/__init__.py
client_file=/app/data/unifi-protect-package/versions/15.12.2/uiprotect/api.py
```

Local source mirror:

```text
data/backend/unifi-protect-package/versions/15.12.2/uiprotect/
```

Command used to verify:

```bash
docker compose exec -T backend sh -lc 'cd /workspace/backend && /app/.venv/bin/python - <<'"'"'PY'"'"'
from importlib import metadata
from pathlib import Path
import inspect
from app.modules.unifi_protect.package import activate_unifi_protect_package_overlay, current_unifi_protect_version, read_active_package_state

activate_unifi_protect_package_overlay()
print("current_unifi_protect_version=" + str(current_unifi_protect_version()))
state = read_active_package_state()
print("active_state.mode=" + str(state.mode))
print("active_state.version=" + str(state.version))
import uiprotect
from uiprotect import ProtectApiClient
print("uiprotect_file=" + str(Path(uiprotect.__file__)))
print("client_file=" + str(Path(inspect.getsourcefile(ProtectApiClient) or "")))
PY'
```

Do not print Protect credentials, cookies, API keys, tokens, raw media, or raw
payloads while debugging this integration.

## IACS Wrappers

All application code should keep using the IACS wrapper layer:

- `backend/app/modules/unifi_protect/client.py`
- `backend/app/services/unifi_protect.py`

The wrapper constructs `ProtectApiClient` with:

```python
ProtectApiClient(
    host=config.unifi_protect_host,
    port=config.unifi_protect_port,
    username=config.unifi_protect_username,
    password=config.unifi_protect_password,
    api_key=config.unifi_protect_api_key,
    verify_ssl=config.unifi_protect_verify_ssl,
    cache_dir=settings.data_dir / "unifi-protect-cache",
    config_dir=settings.data_dir / "unifi-protect-config",
    store_sessions=False,
    subscribed_models={ModelType.CAMERA, ModelType.EVENT},
    ignore_stats=True,
)
```

Important behavior:

- `store_sessions=False`, so the backend logs in rather than persisting Protect
  session cookies.
- `api.update()` is called first to load the private bootstrap.
- `api.update_public()` is called when present to prime the Public Integration
  API cache.
- IACS subscribes to all available raw websocket methods defensively:
  `subscribe_websocket`, `subscribe_events_websocket`,
  `subscribe_devices_websocket`, plus matching state callbacks.
- IACS serializes camera/event model objects through `getattr` and `unifi_dict`
  fallbacks, which helps survive model shape drift.

## Private API Bases

In `uiprotect==15.12.2`, `BaseApiClient` defines:

```text
private_api_path=/proxy/protect/api/
public_api_path=/proxy/protect/integration
private_ws_path=/proxy/protect/ws/updates
events_ws_path=/proxy/protect/integration/v1/subscribe/events
devices_ws_path=/proxy/protect/integration/v1/subscribe/devices
```

Private requests authenticate through UniFi OS login:

```text
POST /api/auth/login
body: {"username": ..., "password": ..., "rememberMe": false}
```

The login response provides a session cookie and usually an `x-csrf-token`.
Private API requests use those headers/cookies. Public Integration API requests
use `X-API-KEY`.

The private request helper builds URLs as:

```text
https://{host}:{port}/proxy/protect/api/{url}
```

and then decodes JSON through `orjson` for object/list helpers.

Private request behavior worth preserving:

- Maintain one `aiohttp.ClientSession` with an unsafe cookie jar so UniFi OS
  auth cookies are retained.
- On private requests, call `ensure_authenticated()` before the request.
- Retry transient HTTP status codes before failing. Current `uiprotect` retries
  408, 429, 500, 502, 503, and 504 with bounded exponential backoff.
- Treat 401 and 403 as authorization failures.
- Treat 400 as a bad request. A global alarm-manager 400 is special upstream,
  but IACS only uses read paths and can treat it as a normal Protect error.
- Treat other 4xx as bad requests and other non-2xx responses as provider
  failures.
- Never log request headers, cookies, credentials, raw media, or raw payloads.

## Private Endpoints IACS Depends On

| IACS behavior | `uiprotect` call | Private endpoint |
| --- | --- | --- |
| Private bootstrap and cached cameras/events | `api.update()` -> `get_bootstrap()` | `GET /proxy/protect/api/bootstrap` |
| Event history lookup | `api.get_events(...)` | `GET /proxy/protect/api/events` |
| Single event lookup | `api.get_event(event_id)` | `GET /proxy/protect/api/events/{event_id}` |
| Live camera snapshot | `camera.get_snapshot(w, h)` | `GET /proxy/protect/api/cameras/{camera_id}/snapshot?w=...&h=...` |
| Package camera snapshot | `camera.get_package_snapshot(w, h)` | `GET /proxy/protect/api/cameras/{camera_id}/package-snapshot?ts=...&force=true&w=...&h=...` |
| Event thumbnail | `api.get_event_thumbnail(event_id, w, h)` | `GET /proxy/protect/api/events/{event_id}/thumbnail?w=...&h=...` |
| Event video clip | `event.get_video()` | `GET /proxy/protect/api/video/export?camera=...&start=...&end=...&channel=...` |
| LPR smart-detect track | `api.api_request_obj(f"events/{event_id}/smartDetectTrack")` | `GET /proxy/protect/api/events/{event_id}/smartDetectTrack` |
| Raw private update websocket | `api.subscribe_websocket(...)` | `WSS /proxy/protect/ws/updates?lastUpdateId=...` |

IACS also uses public API pieces where available:

- `api.update_public()`
- `subscribe_events_websocket`
- `subscribe_devices_websocket`
- `send_alarm_webhook_public`

Those are not the critical private fallback surface, but they may need to stay
enabled because current `uiprotect` uses public event/device websockets for
typed public caches.

## Official Integration API Gap Check

Use this check before deciding whether IACS can remove a private endpoint.
Compare the exact IACS behavior, not just a similarly named Protect feature,
against both the official documentation and the configured console.

Official documentation source checked on 2026-06-29:

```text
https://developer.ui.com/protect/v7.1.83/gettingstarted
https://developer.ui.com/protect/v7.1.83/get-v1cameras
https://developer.ui.com/protect/v7.1.83/get-v1camerasidsnapshot
https://developer.ui.com/protect/v7.1.83/get-v1subscribeevents
```

The official API has these useful equivalents:

| IACS need | Official Integration API status |
| --- | --- |
| API-key authentication | Supported by the Integration API. |
| Application/version probe | Supported by `GET /proxy/protect/integration/v1/meta/info`. |
| Camera list | Supported by `GET /proxy/protect/integration/v1/cameras`. |
| Live camera snapshot | Supported by `GET /proxy/protect/integration/v1/cameras/{id}/snapshot`. Use `channel=package` for package cameras. |
| Public realtime event messages | Documented as `WSS /proxy/protect/integration/v1/subscribe/events`. |
| Public realtime device messages | Documented as `WSS /proxy/protect/integration/v1/subscribe/devices`. |
| Alarm Manager webhook test | Supported by `POST /proxy/protect/integration/v1/alarm-manager/webhook/{id}`. This is a mutation/test path and still needs admin confirmation in IACS. |

The official API does not currently cover these private dependencies:

| IACS need | Official API gap |
| --- | --- |
| Historical event search/backfill | No documented `GET /v1/events` equivalent in Protect API `v7.1.83`. |
| Single historical event lookup | No documented `GET /v1/events/{id}` equivalent. |
| Event thumbnail by event/thumbnail id | No documented event thumbnail endpoint. |
| Event video export/clip recovery | No documented event video/export endpoint. |
| LPR candidate/track extraction | No documented `smartDetectTrack` equivalent. The public event schema exposes `smartDetectTypes` including `licensePlate`, but not the candidate plate text, confidence, matched-name, or `detectedThumbnails` payloads IACS records. |
| Private raw update stream | The official public websockets are useful, but they are not a byte-for-byte replacement for `WSS /proxy/protect/ws/updates?lastUpdateId=...`. |

Read-only live probe used on 2026-06-29:

```text
GET /proxy/protect/integration/v1/meta/info -> 200
GET /proxy/protect/integration/v1/cameras -> 200
GET /proxy/protect/integration/v1/events -> 404
GET /proxy/protect/integration/v1/events/test/smartDetectTrack -> 404
```

The websocket paths must be checked with a websocket client, not a plain HTTP
GET. A normal GET returning 404 does not prove the documented websocket route is
absent.

Current conclusion: IACS can use the official API for camera inventory, public
device/event updates, live snapshots, and alarm-manager webhook tests. It still
needs the private API for historical event recovery, event media, and LPR
candidate details until Ubiquiti exposes official equivalents for those
capabilities.

## Replacement Boundary

If `uiprotect` removes the private API layer entirely, IACS does not need a
full replacement library. Recreate these internal methods on an IACS-owned
adapter and keep the public service interface unchanged:

```python
class IacsProtectClient:
    async def update(self) -> None: ...
    async def update_public(self) -> None: ...
    async def close_session(self) -> None: ...
    async def async_disconnect_ws(self) -> None: ...
    def subscribe_websocket(self, callback): ...
    def subscribe_websocket_state(self, callback): ...
    def subscribe_events_websocket(self, callback): ...
    def subscribe_events_websocket_state(self, callback): ...
    def subscribe_devices_websocket(self, callback): ...
    def subscribe_devices_websocket_state(self, callback): ...
    async def get_events(self, *, start, end, limit, types, sorting): ...
    async def get_event(self, event_id: str): ...
    async def get_camera(self, camera_id: str): ...
    async def get_event_thumbnail(self, thumbnail_id: str, *, width=None, height=None, retry_timeout=2): ...
    async def api_request_obj(self, path: str): ...
```

The camera/event objects returned by this adapter only need the attributes IACS
reads in `backend/app/modules/unifi_protect/client.py`:

- camera: `id`, `name`/`display_name`, `type`/`model`, `state`, adoption flags,
  recording/video-ready booleans, detection booleans, last event ids/times,
  `channels`, `feature_flags`, `smart_detect_zones`, `mac`, `is_dark`,
  `get_snapshot()`, and `get_package_snapshot()`.
- event: `id`, `type`, `camera_id`, `camera`, `start`, `end`, `score`,
  `smart_detect_types`, `thumbnail_id`, `metadata`, and `get_video()`.
- websocket message: `action`, `changed_data`, `new_obj`, and `old_obj`.

The adapter may use plain dataclasses or `types.SimpleNamespace`. It does not
need to recreate pydantic model validation if it preserves these attributes and
keeps failure behavior conservative.

## Bootstrap State

`api.update()` loads:

```text
GET /proxy/protect/api/bootstrap
```

The private bootstrap fields IACS relies on are:

```text
cameras[]
events cache populated from websocket messages
nvr.hosts
lastUpdateId
authUserId / users, only indirectly for upstream permission checks
```

Current `uiprotect` converts bootstrap device lists into dictionaries keyed by
device id. It also builds `id_lookup` and `mac_lookup` maps. If rebuilding in
IACS, camera lookup only needs:

```text
bootstrap.cameras[camera_id]
camera.id
camera.mac
camera.display_name or camera.name
camera.smart_detect_zones[]
```

The `lastUpdateId` is important for private websocket resume:

```text
WSS /proxy/protect/ws/updates?lastUpdateId={bootstrap.last_update_id}
```

When the websocket rejects/closes because the last update id is stale, current
`uiprotect` schedules a fresh `update()` and reconnects.

## Event History Details

`api.get_events(...)` eventually calls:

```text
GET /proxy/protect/api/events
```

Common query parameters:

```text
start={milliseconds since epoch}
end={milliseconds since epoch}
limit={integer}
offset={integer}
types={event type values}
smartDetectTypes={smart detect type values}
orderDirection=ASC|DESC
withoutDescriptions=true|false
categories={category}
allCameras=true|false
```

The IACS wrapper currently calls it with:

```python
events = await api.get_events(
    start=start,
    end=end,
    limit=max(limit * 3, limit),
    types=types,
    sorting="desc",
)
```

and then filters by `camera_id` in IACS.

`uiprotect` filters out unknown event types and only returns device events whose
score is at least the client's `minimum_score`. IACS leaves `minimum_score` at
the package default.

## Smart Detect Track Details

This is the highest-risk private dependency.

IACS calls:

```python
track = await api.api_request_obj(f"events/{event_id}/smartDetectTrack")
```

Used by:

- `UnifiProtectIntegrationService.event_lpr_track`
- `_probe_lpr_track` after websocket LPR-looking events
- snapshot recovery and missed-event diagnostics that compare IACS access
  events with Protect history

Expected useful response shape:

```json
{
  "id": "track-id",
  "event": "... or event object ...",
  "eventId": "protect-event-id",
  "cameraId": "protect-camera-id",
  "payload": [
    {
      "id": "track-item-id",
      "timestamp": 1710000000000,
      "coord": [0, 0, 100, 100],
      "objectType": "licensePlate",
      "zoneIds": [1],
      "duration": 1000,
      "licensePlate": "AB12CDE",
      "confidence": 94,
      "firstShownTimeMs": 0,
      "idleSinceTimeMs": 0,
      "stationary": false,
      "attributes": {
        "color": {"val": "black", "confidence": 90},
        "vehicleType": {"val": "car", "confidence": 87}
      }
    }
  ]
}
```

The upstream `SmartDetectTrack` model in 15.12.2 has:

```text
id: str
payload: list[SmartDetectItem]
cameraId -> camera_id
eventId -> event_id
```

Each `SmartDetectItem` has:

```text
id
timestamp
coord
objectType -> object_type
zoneIds -> zone_ids
duration
confidence
firstShownTimeMs -> first_shown_time_ms
idleSinceTimeMs -> idle_since_time_ms
stationary
licensePlate -> license_plate, optional
depth, optional
speed, optional
attributes, optional map of {val, confidence}
lines, optional
```

IACS is intentionally tolerant. It accepts plate keys including:

```text
licensePlate
license_plate
licencePlate
licence_plate
plate
plateNumber
plate_number
matchedName
matched_name
registration
registrationNumber
registration_number
vrn
```

For LPR timing, rows are read from `smartDetectTrack.payload[]` and candidate
paths are recorded as:

```text
smartDetectTrack.payload[{index}].{key}
```

For vehicle presence and visual detections, the same payload rows are also
examined for:

```text
objectType
object_type
type
attributes
attrs
color / colour / vehicleColor / vehicleColour
vehicleType / vehicle_type / vehicleClass / vehicle_class
```

If this endpoint disappears from `uiprotect` but still exists in Protect, the
smallest replacement inside IACS is a private GET helper on our wrapper:

```python
async def protect_private_json(client, path: str) -> dict[str, Any]:
    data = await client.api_request_obj(path)
    if not isinstance(data, dict):
        raise UnifiProtectError(f"Unexpected Protect response for {path}")
    return data

track = await protect_private_json(client, f"events/{event_id}/smartDetectTrack")
```

If `api_request_obj` is also removed, reimplement with `aiohttp` using the
private authentication sequence in this document.

If the endpoint itself is removed from Protect, there is no equivalent current
IACS source for late-arriving LPR track attributes. In that case the fallback is
to degrade gracefully to webhook payloads, event metadata `detectedThumbnails`,
and snapshot evidence. Do not synthesize access events or hardware decisions
from partial visual evidence.

## Snapshots And Media

Private live snapshot:

```text
GET /proxy/protect/api/cameras/{camera_id}/snapshot?w={width}&h={height}
```

Package camera snapshot:

```text
GET /proxy/protect/api/cameras/{camera_id}/package-snapshot?ts={now_ms}&force=true&w={width}&h={height}
```

Historical package snapshot uses:

```text
GET /proxy/protect/api/cameras/{camera_id}/recording-snapshot?ts={ms}&lens=2&w={width}&h={height}
```

Event thumbnail:

```text
GET /proxy/protect/api/events/{event_id}/thumbnail?w={width}&h={height}
```

`uiprotect` strips an old `e-` prefix from thumbnail/heatmap IDs before calling
the endpoint. IACS already falls back to the event ID when `thumbnail_id` is not
available.

Event video:

```text
GET /proxy/protect/api/video/export?camera={camera_id}&start={ms}&end={ms}&channel={index}
```

For package/second lens video, current `uiprotect` uses `lens=2` when the
channel index is `3`.

## Websocket Details

Private update websocket:

```text
WSS /proxy/protect/ws/updates
WSS /proxy/protect/ws/updates?lastUpdateId={bootstrap.last_update_id}
```

It uses the private login cookie and CSRF token. `uiprotect` decodes binary
frames with an 8-byte header:

```text
packet_type: 1 byte
payload_format: 1 byte
deflated: 1 byte
unknown: 1 byte
payload_size: 4-byte signed int, network byte order
```

If `deflated` is true, the payload is zlib-decompressed. JSON payloads are
decoded with `orjson`. The processed message is a `WSSubscriptionMessage` with:

```text
action: add | update | remove
new_update_id
changed_data
new_obj
old_obj
```

The binary private websocket message contains two frames back-to-back:

```text
action frame
data frame
```

Each frame uses the 8-byte header above. Decode the first frame from byte
offset 0, then decode the second frame from `action_frame.length`.

The action frame JSON contains fields such as:

```text
action: add | update | remove
modelKey: camera | event | nvr | ...
id: object id
newUpdateId: next update id
```

The data frame JSON is the changed object payload or partial update payload.
Current processing updates `last_update_id` from `newUpdateId`, filters to
models `{camera, event}`, and emits a normalized message. For IACS, a minimal
raw websocket replacement can skip full cache mutation as long as it emits:

```python
SimpleNamespace(
    action=action["action"],
    changed_data=data | {"modelKey": action["modelKey"], "id": action["id"], **data},
    new_obj=event_or_camera_object_or_none,
    old_obj=None,
)
```

`uiprotect==15.12.2` also emits a synthetic camera update after it refreshes
cached RTSPS stream metadata. Its changed payload only contains `modelKey`,
`id`, and `rtsps_streams`, while `new_obj` is the full cached camera. IACS
must not treat that message as new LPR, visual-detection, or vehicle-presence
evidence; it may still publish the redacted camera update for observability.

Websocket states are tracked independently for `private`, `events`, and
`devices`. In current `uiprotect`, `CONNECTED.value` is `True` and
`DISCONNECTED.value` is `False`; state handling must normalize the enum name,
not compare its value with the string `"CONNECTED"`. Newer releases also emit
`AUTH_FAILED`, which IACS exposes separately from successful REST/bootstrap
health.

However, camera/event object reconstruction improves IACS behavior because
`_lpr_track_probe_event_id`, `websocket_message_payload`, and detection
recorders inspect `new_obj.id`, `new_obj.model`, `new_obj.smart_detect_types`,
`new_obj.type`, `new_obj.camera`, `new_obj.camera_id`, `new_obj.start`, and
camera detection booleans.

IACS turns those messages into:

- LPR timing observations
- vehicle visual detections
- vehicle presence updates
- realtime `protect.camera.updated` and `protect.event.detected` events

The LPR probe starts when a websocket event looks like an LPR event. Current
heuristics include:

- model is `event`
- event id is present
- `smart_detect_types` contains `licensePlate`
- event type is `smartDetectZone`
- camera name contains `lpr` or `license`
- changed payload text contains `licensePlate`, `detectedThumbnails`, or
  `detected_thumbnails`

The probe retries `smartDetectTrack` because Protect often creates the event
before track payloads are ready.

## Minimal Private Client Reimplementation

Use this only if `uiprotect` removes a wrapper that IACS still needs. Keep this
inside `backend/app/modules/unifi_protect/` or the existing
`backend/app/services/unifi_protect.py` owner.

Skeleton:

```python
from http import HTTPStatus
from typing import Any

import aiohttp
import orjson


class ProtectPrivateClient:
    def __init__(self, *, host: str, port: int, username: str, password: str, verify_ssl: bool) -> None:
        self.base = f"https://{host}:{port}"
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.headers: dict[str, str] = {}
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "ProtectPrivateClient":
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        await self.login()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.session is not None:
            await self.session.close()

    async def login(self) -> None:
        assert self.session is not None
        response = await self.session.post(
            f"{self.base}/api/auth/login",
            json={"username": self.username, "password": self.password, "rememberMe": False},
            ssl=self.verify_ssl,
        )
        if response.status != HTTPStatus.OK:
            raise UnifiProtectError(f"Protect login failed: HTTP {response.status}")
        csrf = response.headers.get("x-csrf-token")
        if csrf:
            self.headers["x-csrf-token"] = csrf

    async def private_bytes(self, path: str, *, params: dict[str, Any] | None = None) -> bytes:
        assert self.session is not None
        response = await self.session.get(
            f"{self.base}/proxy/protect/api/{path}",
            params=params,
            headers=self.headers,
            ssl=self.verify_ssl,
        )
        if not 200 <= response.status < 300:
            raise UnifiProtectError(f"Protect private API failed: {path} HTTP {response.status}")
        return await response.read()

    async def private_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return orjson.loads(await self.private_bytes(path, params=params))
```

For a production replacement, add:

- transient-status retry for 408, 429, 500, 502, 503, and 504
- CSRF header refresh when Protect sends a new `x-csrf-token`
- one forced relogin on 401/403 before declaring the provider unavailable
- explicit `close()`/`async_disconnect_ws()` cleanup
- bounded media timeouts and streaming support for event video if large clips
  are ever exposed beyond current thumbnail/snapshot flows
- a private websocket task with reconnect backoff and a stale-`lastUpdateId`
  path that refreshes bootstrap before reconnecting

Do not log request headers, cookies, credentials, response bodies, media bytes,
or raw payloads. Log endpoint names and status/error summaries only.

## Recovery Checklist

If a newer `uiprotect` release removes or breaks one of these wrappers:

1. Confirm the active package version and overlay state from the running
   backend.
2. Identify which IACS wrapper failed, not just the frontend symptom.
3. Check whether the direct private endpoint still exists on the Protect
   console by adding a temporary, redacted, read-only probe in the wrapper.
4. Prefer adapting `backend/app/modules/unifi_protect/client.py` so the rest of
   IACS keeps using the same service interface.
5. Preserve fail-closed behavior. Missing events, missing track payloads, or
   provider rejection must not trigger hardware side effects.
6. Run targeted tests around:
   - `backend/tests/test_unifi_protect.py`
   - `backend/tests/test_unifi_protect_client.py`
   - `backend/tests/test_unifi_protect_updates.py`
   - `backend/tests/test_lpr_timing.py`
   - `backend/tests/test_vehicle_visual_detections.py`
   - `backend/tests/test_restart_backfill.py`
   - `backend/tests/test_access_events.py`
7. Verify through the running Compose backend before trusting the change.

Recommended smoke checks after any replacement:

```bash
docker compose exec -T backend sh -lc 'cd /workspace/backend && /app/.venv/bin/python -m compileall -q app'
./scripts/backend-pytest tests/test_unifi_protect.py tests/test_lpr_timing.py tests/test_vehicle_visual_detections.py tests/test_restart_backfill.py
curl -fsS http://localhost:8089/api/v1/health
curl -fsS http://localhost:8089/api/v1/integrations/unifi-protect/status
```

Do not run live gate, garage, or provider actuation while validating Protect
read paths.
