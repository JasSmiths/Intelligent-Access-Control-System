# Phase 2: Data Models and LPR Debounce

## Delivered

- Core SQLAlchemy models:
  - Groups and profile categories.
  - People and one-to-many vehicle ownership.
  - Time slots and schedule assignments for groups or people.
  - Presence state per person.
  - Access events with direction, decision, confidence, source, and timing class.
  - Anomalies for unauthorized plates, duplicate state transitions, and schedule violations.
- Startup database bootstrap for early development.
- Demo seed data:
  - Steph, Family, `STEPH26`, all-day schedule.
  - Bob, Contractor/Gardener, `BOB123`, Wednesday 08:00-12:00 schedule.
- Ubiquiti webhook normalization remains isolated in `app/modules/lpr/ubiquiti.py`.
- Debounce worker:
  - Queues rapid LPR reads.
  - Groups similar plate candidates from the same source.
  - Waits for a quiet window or max debounce window.
  - Selects the highest-confidence final read.
  - Persists one final access event.
- Historical timing classifier:
  - Uses prior granted events for the same person and direction.
  - Labels events as earlier than usual, normal, later than usual, or unknown.

## Useful Endpoints

- `POST /api/v1/webhooks/ubiquiti/lpr`
- `POST /api/v1/simulation/arrival/{registration_number}`
- `POST /api/v1/simulation/misread-sequence/{registration_number}`
- `GET /api/v1/events`
- `GET /api/v1/presence`
- `GET /api/v1/anomalies`
- `WS /api/v1/realtime/ws`

## Debounce Settings

Configured through environment variables:

- `IACS_LPR_DEBOUNCE_QUIET_SECONDS`
- `IACS_LPR_DEBOUNCE_MAX_SECONDS`
- `IACS_LPR_SIMILARITY_THRESHOLD`

These defaults favor correctness over instant action during the early build:
wait briefly for confidence to improve, then emit one final event.

## Reverse Proxy Notes

The host-facing backend port defaults to `8088`. Nginx Proxy Manager should
proxy to:

```text
http://<docker-host-ip>:8088
```

The container still listens internally on `8000`, and Uvicorn is started with
proxy-header support for `X-Forwarded-Proto`, `X-Forwarded-For`, and host
forwarding.
