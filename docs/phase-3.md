# Phase 3: Integrations and Notifications

## Current State

- Home Assistant integration includes a shared REST/WebSocket client, gate cover
  control, garage/cover helpers, TTS announcements, state sync, and maintenance
  mode synchronization to `input_boolean.top_gate_maintenance_mode`.
- Gate opens from LPR, automations, Alfred, notifications, and admin actions go
  through `GateCommandCoordinator`; do not bypass durable command records.
- Apprise and Home Assistant mobile notification senders implement the normalized
  notification sender contract.
- Notification rules are stored in `notification_rules`, not `system_settings`.
  Templates use `@Variable`; bracket tokens remain accepted for compatibility.
- Actionable notification context is stored in `notification_action_contexts`
  with HMAC tokens, short TTLs, and one-time consume semantics.
- WhatsApp Cloud API, Discord, DVLA VES, UniFi Protect, iCloud Calendar, and
  dependency-update workflows are now part of the integration surface.

## Configuration

Integration values are dynamic settings owned by the Settings UI/API. Bootstrap
environment should stay limited to runtime selectors and auth/bootstrap wiring.
Secrets such as Home Assistant tokens, Apprise URLs, Discord/WhatsApp tokens,
DVLA keys, UniFi credentials/API keys, and LLM provider keys are encrypted in
`system_settings`.

Legacy environment values may seed defaults during development, but future work
should read and write integration configuration through Settings UI/API.

Home Assistant `person.*` geofence/entity states are not used as IACS presence.
Access-event entry/exit records are the presence source of truth.

## API Endpoints

- `GET /api/v1/integrations/home-assistant/status`
- `POST /api/v1/integrations/gate/open`
- `POST /api/v1/integrations/cover/command`
- `POST /api/v1/integrations/announcements/say`
- `POST /api/v1/integrations/notifications/test`
- `GET/POST /api/v1/webhooks/whatsapp`
- `GET/PATCH/POST /api/v1/integrations/discord/*`
- `GET/POST/DELETE /api/v1/integrations/unifi-protect/*`
- `GET/POST/DELETE /api/v1/integrations/icloud-calendar/*`
- `GET/POST /api/v1/dependency-updates/*`

State-changing integration actions require Admin confirmation/audit where the
action can affect hardware, external messaging, maintenance mode, update
application, backups, or connection tests.

## Nginx Proxy Manager

Use the frontend service on host port `8089` as ingress:

```text
http://<docker-host-ip>:8089
```

Enable WebSocket support for realtime, Alfred chat, and dependency job streams.
