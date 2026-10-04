# ESPHome access-device integration

ESPHome is a supported production provider behind IACS access-device owners.
Home Assistant is not required for an ESPHome binding. Configure it through
IACS Integrations and access-device configuration, with Admin confirmation and
durable audit. Read [hardware safety](docs/agent/hardware-safety.md) before testing.

## Configuration and ownership

Dynamic setting `esphome_devices` contains each device's `id`, `name`, `host`,
`port` (default `6053`), `encryption_key`, `timeout_seconds` and `enabled` flag.
The setting is encrypted as a secret by `backend/app/services/settings.py`.
Keep credentials out of Markdown, logs, frontend code and committed config.

The provider `backend/app/modules/access_devices/esphome.py` discovers covers,
maintains native API sessions and state subscriptions, and implements commands.
`backend/app/services/access_device_configuration.py` owns device bindings,
provider selection and frozen target plans. A cover's device identity and selector
belong to its binding; do not hard-code a local address, name or entity key.

Gate opens use `GateCommandCoordinator` through
`backend/app/modules/gate/access_devices.py`. Garage/cover commands use
`AccessDeviceService`. The provider itself is not a user-facing command path.
Command receipts persist separately from live device state; native API write
acceptance does not prove the gate or garage moved.

## Native API handshake

The current provider constructs `aioesphomeapi.APIClient` with host, port and
`noise_psk`, then performs `start_resolve_host()`, `start_connection()` and
`finish_connection(login=False)`. It does not send a legacy password login.
Preserve this encrypted-handshake behavior; adding an empty-password login can
cause a `HelloResponse`/`ConnectResponse` timeout.

The provider uses bounded connection/command budgets, a live-stream path and a
cold-connect path. Keep observation freshness, pending verification, cleanup and
shutdown fencing intact. An uncertain write must remain visible for reconciliation
rather than triggering another send.

## Validation

Use fake-provider tests through the [isolated harness](docs/validation/phase1.md),
including access-device, configuration, target-plan and command-journal tests.
Read-only status/discovery may contact the configured device; do not substitute
it for an isolated test. Live command validation requires a separately requested
supervised test, explicit local confirmation and IACS Admin confirmation.
