# UniFi Protect integration contract

Use this when changing Protect camera/event/media handling or diagnosing a
wrapper regression. Current application contracts below were checked against the
working tree on 2026-10-04. This is not a live-console validation report.

## Owners and dependency

- Protocol wrapper: `backend/app/modules/unifi_protect/client.py`.
- Integration lifecycle, event evidence and track probes:
  `backend/app/services/unifi_protect.py`.
- API adapter: `backend/app/api/v1/unifi_protect.py`.
- Dependency source: `backend/pyproject.toml`, currently `uiprotect==16.10.0`.

The dependency is image-installed. Change it through the repository build and
release process; there is no runtime updater or disk package overlay. Keep vendor
I/O behind the wrapper rather than recreating Protect calls in API, access or
API handlers. Never expose credentials, cookies, API keys, raw media or raw
provider payloads in diagnostics.

## Client and lifecycle

The wrapper requires host, username, password and API key, and builds
`ProtectApiClient` with the configured port/TLS policy, data-directory cache and
config paths, `store_sessions=False`, subscribed models `{CAMERA, EVENT}` and
`ignore_stats=True`. It loads `api.update()` followed by `update_public()` when
available. Session cookies are not persisted by the wrapper.

Register state listeners before message listeners. The wrapper subscribes to
available private, public-event and public-device websocket channels separately.
Shutdown unsubscribes and closes sockets plus private/public sessions through
`close_unifi_protect_client`; preserve cleanup when changing package interfaces.

REST/bootstrap health does not prove websocket health. Preserve separate channel
state and authentication failure reporting. Normalize websocket state enum names;
a boolean-valued `CONNECTED` enum is not the string `"CONNECTED"`.

## Application dependencies

| Behavior | Wrapper/upstream call |
| --- | --- |
| Bootstrap and camera inventory | `load_unifi_protect_bootstrap`, `list_bootstrap_cameras`, `bootstrap_camera` |
| Camera lookup | `get_camera_by_identifier` |
| Historical event lookup | `list_unifi_protect_events` → `api.get_events` |
| Single event | `get_event_by_id` → `api.get_event` |
| Live/package snapshot | `get_unifi_protect_snapshot` → `camera.get_snapshot` / `get_package_snapshot` |
| Event thumbnail | `get_unifi_protect_event_thumbnail` → `api.get_event_thumbnail` |
| Event video | `get_unifi_protect_event_video` → `event.get_video` |
| LPR track | `event_lpr_track`, `_probe_lpr_track` → `api.api_request_obj("events/{id}/smartDetectTrack")` |
| Realtime evidence | `subscribe_unifi_protect`, `websocket_message_payload` |
| Alarm webhook test | `send_alarm_webhook_test` → `send_alarm_webhook_public` |

Event history defaults to the preceding 24 hours through ten seconds ahead of
current UTC time. It asks upstream for `max(limit * 3, limit)` descending events,
then filters by camera and truncates to the requested limit. Preserve this behavior
or explicitly change the caller contract and tests. An unsupported event type is
an error, not an unfiltered query.

Camera/event serialization uses defensive attribute and `unifi_dict` access.
Keep redaction and optional-field handling in the wrapper. Do not spread package
model dependencies into domain services.

Alarm webhook tests are effects. They require IACS Admin confirmation and audit;
they are not health checks. See [hardware safety](agent/hardware-safety.md).

## LPR tracks and evidence

`smartDetectTrack` is a critical private dependency. The service probes it after
LPR-looking websocket events, retrying reads because Protect can announce an event
before its track is ready. Preserve bounded probing and provider-error handling.

The useful wire shape is a track object with `eventId`, `cameraId` and `payload[]`.
Rows may carry `timestamp`, `objectType`, `licensePlate`, `confidence`, `zoneIds`,
`coord`, and vehicle attributes such as color/type with confidence. IACS handles
shape variants including snake-case plate keys and `detectedThumbnails` metadata.
Track paths such as `smartDetectTrack.payload[0].licensePlate` identify evidence;
they are not authorization by themselves.

Keep extraction with its owners in `backend/app/services/lpr_timing.py`,
`backend/app/services/vehicle_visual_detections.py` (including presence tracking). Camera stream metadata
updates containing only `rtsps_streams` must not count as new LPR, visual or
presence evidence, even when `new_obj` is a complete cached camera.

Missing events/tracks or partial visual evidence cannot create hardware authority.
Preserve webhook/event/snapshot fallback behavior and explain missing evidence;
do not fabricate access events, confidence or movement direction. Historical
recovery remains hardware-free.

## Private protocol reference

These wire details are retained from the earlier adapter investigation. They are
useful when checking a regression, but are not a claim about every installed
Protect version. Reverify the affected contract against the pinned package and,
when authorized, the configured console before replacing a wrapper.

| Purpose | Observed endpoint |
| --- | --- |
| UniFi OS session login | `POST /api/auth/login` |
| Private bootstrap | `GET /proxy/protect/api/bootstrap` |
| History / single event | `GET /proxy/protect/api/events`, `/events/{id}` |
| LPR track | `GET /proxy/protect/api/events/{id}/smartDetectTrack` |
| Live snapshot | `GET /proxy/protect/api/cameras/{id}/snapshot` |
| Package snapshot | `GET /proxy/protect/api/cameras/{id}/package-snapshot` |
| Event thumbnail | `GET /proxy/protect/api/events/{id}/thumbnail` |
| Video export | `GET /proxy/protect/api/video/export` |
| Private updates | `WSS /proxy/protect/ws/updates?lastUpdateId=...` |
| Public event/device subscriptions | `WSS /proxy/protect/integration/v1/subscribe/events`, `/devices` |

Private login uses username/password with `rememberMe=false`; private requests
use the returned cookie and CSRF header. Public Integration API calls use
`X-API-KEY`. Session/TLS/timeout/error handling must stay in the provider layer.
Reject authorization and provider failures; never interpret an error as empty
successful evidence. Bounded retries for read-only requests must not be copied
into command or alarm-test paths.

History query times use milliseconds since epoch, with limit/offset, event type
and sort direction parameters. Snapshot/thumbnail dimensions use `w` and `h`.
Video export uses camera, start/end timestamps and channel. Preserve event versus
thumbnail identifier handling, including upstream handling of an `e-` prefix.
These are provider details, not `/api/v1` application routes.

Bootstrap supplies camera lookup and `lastUpdateId`. Private websocket messages
contain action and data frames, each with an eight-byte header: one byte each for
packet type, payload format, compression flag and reserved field, followed by a
four-byte network-order payload length. Compressed payloads use zlib; decoded
action metadata includes action, model key, object ID and new update ID. Upstream
normalizes messages to action, changed data, new object and old object. Preserve
resume behavior and fresh-bootstrap recovery when the update ID becomes stale.

Public camera inventory, snapshots or realtime streams do not automatically
replace private history, event media or LPR candidate tracks. Compare exact
required fields and timing before migrating an endpoint. Check current official
Protect documentation for the target version at that time; endpoint availability is version-specific. A plain HTTP GET cannot validate a
websocket endpoint.

## Regression workflow

1. Identify the failing wrapper and package version from the repository/build
   evidence; distinguish camera inventory, history, track, media and socket state.
2. Reproduce with fakes or sanitized fixtures. Adapt the wrapper while preserving
   normalized data, redaction, lifecycle and failure behavior for its callers.
3. Use the [isolated harness](validation/phase1.md), including relevant tests:
   `backend/tests/test_unifi_protect.py`, `test_unifi_protect_client.py`,
   `test_lpr_timing.py`, `test_vehicle_visual_detections.py`,
   `test_restart_backfill.py` and `test_access_events.py`.
4. Report isolated results and any remaining live-provider uncertainty separately.
   Live integration tests, notification tests and hardware commands require their
   own authorization. Do not start/reconfigure production to validate documentation.

Do not implement a speculative replacement client from this reference. If a
required upstream interface changes, keep the smallest necessary adaptation in
`backend/app/modules/unifi_protect/`, verified against its callers and tests.
