# Integrations and safety

IACS owns authorization, durable receipts and audit. Application composition
binds integration observation callbacks to business owners without vendor
back-imports. Binding is inert; the application lifespan starts and stops services. Provider modules translate
approved operations into vendor I/O. Use the application owners even when a
provider offers a simpler direct command. See [architecture](architecture.md)
for the access and movement flow and [validation](validation.md) for isolated
checks.

## Implemented integrations

| Integration | Current use and configuration |
| --- | --- |
| Home Assistant | Cover discovery, gate/garage commands and state observations; mobile notifications, TTS announcements and resident recovery trackers. Configure the URL and token, then select device bindings and notification/announcement targets. [Client](../backend/app/modules/home_assistant/client.py), [cover provider](../backend/app/modules/access_devices/home_assistant.py). |
| ESPHome | Direct native API cover discovery, commands and state subscriptions; Home Assistant is not required for these bindings. Configure enabled devices with host, port (default `6053`), encryption key and timeout, then bind their covers to IACS access devices. [Provider](../backend/app/modules/access_devices/esphome.py). |
| UniFi Protect | Camera discovery, events, snapshots, thumbnails, clips and current camera evidence. The client requires host, local username, password **and** Integration API key, with configurable port and TLS verification. It uses the locked `uiprotect` dependency and its private/public channels. [Service](../backend/app/services/unifi_protect.py), [wrapper](../backend/app/modules/unifi_protect/client.py). |
| Camera image analysis | OpenAI, Gemini, Claude/Anthropic and vision-capable Ollama models analyze images through their native APIs. The `local` selection does not support image analysis. Configure the provider, model, endpoint and applicable credentials. [Providers](../backend/app/ai/providers.py). |
| Notifications | Home Assistant mobile services and Apprise URLs deliver messages through the durable notification service. Rules and automations select recipients and actions; configured URLs can contain credentials. [Delivery owner](../backend/app/services/notifications.py), [adapters](../backend/app/modules/notifications/). |
| iCloud Calendar | Authenticated calendar accounts synchronize calendar events into visitor-pass workflows, including account authentication and 2FA when required. Account state belongs to the calendar service. [Service](../backend/app/services/icloud_calendar.py). |
| DVLA | Tax and basic vehicle details through the [shared vehicle information owner](../backend/app/services/vehicle_information.py). API key, endpoint and timeout retain their separate configuration and connection test. |
| DVSA | Optional MOT history and model enrichment through the same owner. Disabled by default; uses OAuth and an API key. [Adapter](../backend/app/modules/dvsa/client.py), [configuration and behavior](#vehicle-information-and-mot-history). |

ESPHome uses the encrypted native handshake with `noise_psk` and
`finish_connection(login=False)`. Keep vendor calls inside the adapter; adding a
legacy password login is not part of this contract. Both access-device providers
keep command delivery separate from fresh physical state evidence.

## Configuration and secrets

[Bootstrap configuration](../backend/app/core/config.py) supplies deployment
values. Product integration settings are stored in `system_settings` through the
[settings service](../backend/app/services/settings.py); its
`DEFAULT_DYNAMIC_SETTINGS` and `SECRET_KEYS` are the supported setting catalog.
Unknown setting keys are rejected. Use the settings and integrations UI/API
instead of editing database rows or introducing aliases.

Secrets are encrypted with a Fernet key derived from the active authentication
root secret. Preserve that secret with backups; losing it prevents decryption.
Public setting responses indicate whether a secret is configured rather than
returning it. The ESPHome device list is encrypted because it includes device
keys. Never include credentials, cookies, capability tokens or raw provider
payloads in documentation, logs or diagnostic output.

[AccessDeviceConfiguration](../backend/app/services/access_device_configuration.py)
owns device identity, provider bindings and frozen target plans. A confirmed plan
must not be rebuilt from changed settings during dispatch. Provider discovery is
not permission to make a discovered device commandable or an automatic admission
target.

## LPR ingress

UniFi webhook input enters at `POST /api/v1/webhooks/ubiquiti/lpr`.
[Webhook security](../backend/app/services/lpr_webhook_security.py) requires both
the configured `X-IACS-LPR-Token` and an allowed source IP/CIDR. Forwarded client
addresses are trusted only when the immediate peer is a configured trusted
proxy. Missing security configuration fails closed.

The [route](../backend/app/api/v1/webhooks.py) validates and normalizes input
before durable access processing. A `202` response acknowledges intake, not an
access grant or gate movement. Unknown plates never actuate hardware. Maintenance
ignores incoming reads and clears queued work without creating access decisions,
presence or commands from those reads. Suppressed reads outside that boundary
remain durable and explainable.

## Commands, confirmation and evidence

Gate opens use
[GateCommandCoordinator](../backend/app/services/gate_commands.py); garage/cover
commands use [AccessDeviceService](../backend/app/services/access_devices.py).
Manual hardware actions require an authenticated Admin and application
confirmation. Access-device configuration, maintenance, announcements, schedule
overrides, notification sends/tests, workflow edits, integration tests and
telemetry purge also require their Admin confirmation and durable audit.
Automated hardware actions use the same command owners and recheck their current
authorization.

Access and movement decisions commit before dispatch. Presence follows committed
decisions and the applicable admission evidence. Automatic garage fanout requires
verified gate admission, the configured `fanout` policy and current target and
schedule authorization. Provider acceptance alone cannot establish admission.

Inspect durable Admin receipts at
`/api/v1/integrations/gate/commands` and
`/api/v1/integrations/cover/commands`, including `/{command_id}`. Keep delivery,
mechanical confirmation, admission verification and reconciliation distinct.
Provider rejection is failure; accepted-but-unverified or uncertain outcomes
remain reconcilable. Realtime state and logs are context, not audit history.
Never repeat an accepted, pending or ambiguous command, bypass IACS through a
vendor API, delete command/audit rows, or mark success without current evidence.

## Recovery boundaries

Restart/backfill and historical movement repair never replay hardware. Use
[movement reconciliation](../backend/app/services/movement_reconciliation.py) and
the existing command journals to reconcile evidence. If there is no owner path
for a repair, report the proposed mutation before considering raw SQL.

[Missed-exit recovery](../backend/app/services/resident_recovery.py) defaults
disabled. For an opted-in resident with an exact known plate and a conflicting
prior entry, access evidence checks a bounded phone journey, then current camera
evidence if needed. Unresolved eligible denials can offer a scoped, expiring
resident notification action. Each path retains current identity, schedule and
gate policy and uses the same command owner. Resident approval preserves the
original denial and creates a linked recovery event. Recovery never invents an
exit or bypasses unknown-plate, pending-command or admission checks.

Notification delivery also retains attempted and uncertain outcomes durably.
Unknown results require review without automatic resend; historical unfinished
delivery records are not permission to send again.

## Supervised live testing

Code edits and isolated fake-provider tests are authorized development work.
Live provider sends/tests, deployment, production migrations and hardware
operation require separate authorization. A simulator injection or webhook POST
can reach hardware; synthetic input does not make it an inert test.

For a user-requested supervised hardware test:

1. Inspect current health, Admin authentication, provider/device status and
   durable receipts. Resolve pending/leased commands, reconciliation requirements
   and unresolved admission before issuing another command.
2. State the exact target and effect, including every gate/garage reachable by
   the configured plan, and confirm that execution uses the audited IACS owner.
3. Require the operator to type `LIVE_HARDWARE_TEST_CONFIRMED` in the local
   session after that scope is stated. This is additional to the application's
   Admin confirmation.
4. Issue the authorized command once through IACS. Inspect command/audit records
   and current evidence, including delayed reconciliation if needed.
5. Report delivery and verification separately and check for duplicate or stuck
   work. Closing hardware needs a separate request.

Status/discovery requests may contact a configured provider. Use the
[isolated harness](validation.md) for regression validation rather than live
integration tests or simulation endpoints.

## Vehicle information and MOT History

DVLA supplies tax and basic vehicle details; optional DVSA MOT History supplies
vehicle models and normalized test history. The shared
[vehicle information owner](../backend/app/services/vehicle_information.py)
coordinates both. Configure DVSA in Integrations with the client ID, client
secret, API key, Microsoft token URL supplied by DVSA, and OAuth scope
`https://tapi.dvsa.gov.uk/.default`. It defaults disabled. The endpoint is the
[official registration API](https://documentation.history.mot.api.gov.uk/mot-history-api/api-specification/).
Secrets use the existing encrypted settings and masked public responses. Never
copy registration-email credentials into source, fixtures, or documentation.
Connection tests require the existing Admin confirmation and audit.

Fresh usable DVSA MOT dates take precedence, then fresh DVLA information, then
retained data explicitly marked stale. The first-test due date is used when
provided; latest test result and certificate expiry remain separate facts.
A failed latest test is shown even if an earlier passing test has a future expiry.
Missing records and provider errors never imply an expired MOT, and MOT data never
grants or denies access. Provider expiry/due dates are evaluated in the site timezone.

Live eligible arrivals enqueue optional refresh work after access execution.
Unknown plates use a temporary cache and a compact event summary; they do not
create directory vehicles. The database worker claims at most two jobs from a
bounded page, leases them for 120 seconds and expires them after five minutes.
It never owns hardware or presence. Completion preserves newer information and
registration edits and atomically reserves existing MOT/tax notification intents.
Their originating deadline is checked again before delivery. Historical replay
does not enqueue provider jobs. Recovery hold leaves the worker stopped.

Provider results are shared through Redis for the site-local day. Same-plate
requests coalesce, not-found results wait six hours, and failures back off for
1, 5 and 15 minutes. Deferred jobs release their lease and retry within their
five-minute deadline. Cache entries expire within 24 hours. DVSA requests share a
five-per-second, burst-five limit and honour `Retry-After`; manual refresh cannot
bypass coordination. Redis failure defers lookups instead of sending without a
limit. Saved registered-vehicle snapshots remain available. Top Charts reads
this cache without starting provider requests.

`POST /api/v1/integrations/vehicles/lookup` is an Admin setup lookup;
`POST /api/v1/vehicles/{id}/refresh-information` is a confirmed, audited Admin
refresh. `GET /api/v1/vehicles/{id}/mot-history?limit=10` reads saved history,
returns `items`, `total`, and `next_cursor`, and never calls DVSA. Pages allow
1–50 tests and cursors are invalidated when the snapshot changes. Full history
is excluded from directory summaries. Client-submitted MOT/tax fields and lookup
timestamps are no longer accepted by vehicle creation/update contracts.
The old DVLA-only lookup and refresh routes have been retired.

Enabling the integration does not sweep existing vehicles. They refresh on the
next eligible arrival, setup lookup or manual action. Deployment, production
migration, enabling credentials and live provider tests require separate authorization.
