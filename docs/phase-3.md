# Phase 3: Home Assistant and Notifications

## Delivered

- Shared Home Assistant client:
  - REST service calls.
  - State reads.
  - WebSocket `state_changed` subscription with reconnect behavior.
- Home Assistant gate controller:
  - Configurable gate entity.
  - Configurable open service, defaulting to `cover.open_cover`.
  - Gate state mapping into IACS states.
- Home Assistant TTS announcer:
  - Configurable service, defaulting to `tts.cloud_say`.
  - Configurable default media player, for example
    `media_player.all_google_home_speakers`.
- Home Assistant state sync:
  - Broadcasts gate state changes over the realtime event bus.
  - Broadcasts configured door and cover state changes over the realtime event
    bus.
- Apprise notification sender:
  - Comma-separated or newline-separated URL config.
  - Non-blocking dispatch through a worker thread.
- Notification composer:
  - Accepts structured event context.
  - Produces deterministic text now.
  - Leaves a clean contract for Phase 4 AI naturalization.
- Anomaly notification wiring:
  - Unauthorized plates and schedule violations now produce contextual
    notification events.

## Configuration

```env
IACS_HOME_ASSISTANT_URL=http://homeassistant.local:8123
IACS_HOME_ASSISTANT_TOKEN=<long-lived-access-token>
IACS_HOME_ASSISTANT_GATE_ENTITY_ID=cover.driveway_gate
IACS_HOME_ASSISTANT_GATE_OPEN_SERVICE=cover.open_cover
IACS_HOME_ASSISTANT_TTS_SERVICE=tts.cloud_say
IACS_HOME_ASSISTANT_DEFAULT_MEDIA_PLAYER=media_player.all_google_home_speakers

IACS_APPRISE_URLS=pover://user@token
```

Home Assistant `person.*` geofence/entity states are not used as IACS
presence. Access-event entry/exit records are the presence source of truth.

## API Endpoints

- `GET /api/v1/integrations/home-assistant/status`
- `POST /api/v1/integrations/gate/open`
- `POST /api/v1/integrations/announcements/say`
- `POST /api/v1/integrations/notifications/test`

## Nginx Proxy Manager

The backend still publishes on host port `8088`:

```text
http://<docker-host-ip>:8088
```

For WebSockets, enable WebSocket support in NPM for the future frontend chat and
live event stream routes.
