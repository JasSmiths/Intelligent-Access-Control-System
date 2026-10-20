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

Managed ESPHome and Apprise additions/edits, and Home Assistant discovery merges,
apply to fresh configuration within the settings transaction. Discovery I/O runs
before SQL locks. Their responses describe the configuration accepted at commit.
Apprise deletion requires both the selected destination's opaque `destination_id`
and the displayed list's opaque `revision`, bound into confirmation together with
its index. Index-only clients must refresh/update; there is no index-only fallback.
Any change to normalized list order, entries or duplicate count returns 409 for an
old approval. A fresh selection of a duplicate removes one occurrence. The client
must refresh and obtain a new confirmation after a conflict, without automatic
retry. Public previews contain service labels, never credential prefixes; the
bindings are keyed fingerprints and do not reveal URL credentials.

[AccessDeviceConfiguration](../backend/app/services/access_device_configuration.py)
owns device identity, provider bindings and frozen target plans. A confirmed plan
must not be rebuilt from changed settings during dispatch. Provider discovery is
not permission to make a discovered device commandable or an automatic admission
target.

[Binding policies](../backend/app/modules/access_devices/binding_policies.py)
validate provider configuration and supply identity/frozen-setting inputs without
I/O. The runtime registry must cover exactly the same providers. Configuration
and setting changes still acquire the existing target journal locks before setting
locks and rows; unresolved commands block mutation. Secret null/empty updates
retain stored values except clearable `apprise_urls`.

Every settings save validates the complete prospective configuration before any
setting write, even when the admission gate is unset. Numeric strings and the
supported boolean spellings remain accepted; malformed enums, timezones,
non-finite numbers, contradictory debounce windows and invalid structured
provider settings are rejected without echoing submitted values. Blank protected
secrets retain their ciphertext; Apprise URLs can be explicitly cleared.

The canonical constraints are in
[settings validation](../backend/app/services/settings_validation.py). Auth session
minutes are 5–43,200, remember days 1–365; LPR quiet/max windows are 0.5–60 / 1–300
seconds with quiet ≤ max, vehicle idle time 1–86,400 seconds and similarity 0–1.
DVLA/DVSA timeouts are 1–120 / 1–20 seconds and image-analysis timeout 1–300 seconds.
Protect ports are 1–65,535 and snapshot width/height 160–4,096 / 90–2,160 pixels.
Coordinates must be finite, paired and within latitude/longitude bounds.

Invalid legacy settings are never rewritten or interpreted as permission. An
Admin can inspect `/api/v1/settings/validation` and repair the named fields in one
confirmed update. Authentication reads only its canonical settings projection,
so unrelated broken integration/policy values do not block repair login. Invalid
authentication settings still fail closed; a still-valid bearer session can reach
the diagnostic and repair endpoint without cookie configuration. If no such
session exists, keep execution stopped and arrange an explicitly authorized,
backup-first offline repair; this development task does not authorize that write.

A PATCH returns `saved`, public `settings`, actual `changed_keys` and an
`activation` receipt. Persistence/audit acceptance is separate from process-local
provider activation (`not_required`, `activated`, `failed`, `partial_failure` or
`pending`). Identical saves and blank secret placeholders do not restart services.
The [activation owner](../backend/app/services/settings_activation.py) serializes
application saves and restarts, and drains before provider/database disposal.
Activation failure leaves the saved values accepted: inspect service status and
resolve the activation problem before using the integration. Do not blindly
resubmit the same settings. Connection tests retain Admin confirmation/audit;
the [test dispatcher](../backend/app/services/integration_testing.py) delegates
vendor I/O to the appropriate adapters.

## Calendar retirement and upgrade

iCloud Calendar is retired. Account login/2FA, calendar discovery and sync,
workers, routes, workflow actions, settings editors and the SDK dependency are
removed. Manually created visitor passes, schedules, automations and notifications
remain supported. Historical pass source labels explicitly say retired.

Forward migration [0016](../backend/alembic/versions/20261014_0016_retire_icloud_calendar.py)
drops dedicated account/session ciphertext and sync tables. It archives sanitized
sync statuses and per-account checkpoint counts in `system.feature_retirement`
audit records; account names, Apple IDs and raw provider errors are excluded.
It cancels unused/eligible ACTIVE or SCHEDULED source passes without deleting
visits, events, reservations or historical linkage. Departed duration passes lose
future arrival authority. USED one-time passes with an open visit remain available
for departure matching. Provenance disagreement, unresolved reserved/held work,
active open visits and inconsistent active consumption block the entire upgrade
with the affected pass UUID. Reconcile those exact records deliberately; never
infer whether an uncertain gate command happened from this error.

Saved affected workflows are disabled with schedules cleared. Captured affected
work, including mixed hardware/calendar plans and orphan runs, enters terminal
`review_required` with `icloud_calendar_retired`; claims/leases are cleared, whole
plans and accepted/unknown receipts remain. Completed histories remain readable,
with embedded calendar checkpoint details sanitized. Pending calendar confirmations
are cancelled. Existing unsupported-action guards also hold pending plans before
any action runs. No calendar operation can be submitted to the new catalog.

Production upgrade is a separate operator action; development validation never
runs it. Stop and drain every old backend/worker before cutover: mixed old/new
workers are unsupported. Take and verify a matching database, configured data
files/auth-root and image backup. Inspect open/uncertain visitor work first. Run
the normal Alembic upgrade with the new image while workers remain stopped; a
blocker rolls back all retirement SQL, including credential deletion. Correct only
the identified provenance/reservation/visit condition through its established owner
and retry after deliberate review. Start the new version only after migration and
schema checks succeed. Review disabled workflows and create a supported replacement
explicitly; never remove one retired step and replay the remaining hardware plan.

After a successful upgrade and verified backup, an operator may inspect only the
`icloud-calendar-sessions` child of the deployment's configured `IACS_DATA_DIR`.
Confirm it is the retired session directory, is not a symlink, and no old process
uses it. Remove that exact directory offline if desired; never delete the parent,
auth-root files, other provider state or backups. The migration does not touch
files, and this task does not perform that cleanup. Old browser UUID-only calendar
request references may be cleared from local/session storage by exact
`iacs-calendar-run:` keys; they contain no executable capability in the new UI.

Downgrade cannot recreate deleted credentials or data. Rollback requires stopping
the new workers and restoring the matching pre-upgrade database/files and old image
pair; otherwise fix forward. Preserve the backup until the operator accepts the
new deployment. No production migration, deployment or live Apple test is part of
local remediation.

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

Protect discovery now keeps its active interval, captured page and queued interval
in `historical_discovery_progress`. The discovery owner in
[historical_discovery.py](../backend/app/services/historical_discovery.py) fences
short transactions with a database-clock lease. Candidate acceptance and page
advancement commit together. Provider calls run outside SQL locks; configuration
and provider generation are checked again at acceptance. Startup and periodic
recovery share this progress. Candidate processing errors remain separate from
discovery coverage and never authorize hardware or notification replay.

Each discovery cycle admits at most four raw pages, 32 event records and 36
list/detail/track reads (a track operation reserves two reads), with a ten-second
discovery deadline including connection preparation.
The existing candidate drain separately retains its 32-item bound and 20-second
media deadline. A saturated raw page splits its fixed millisecond interval; a
saturated one-millisecond boundary is held as **coverage unproven**, including
when adjacent boundary events caused saturation. It never advances as complete.
Raw HTTP allocation still belongs to the SDK. Underfull completion assumes the
provider honors its limit and time bounds; it cannot prove coverage against silent
truncation, retention deletion or arbitrarily late insertion. Two-minute overlap
rescans reduce late-insertion risk. A merged requested horizon beyond seven days
is held; normal startup recovery remains bounded to its existing lookback.

Detailed health exposes `historical_discovery_pending`,
`historical_discovery_held` and a sanitized hold reason; recovery audits also retain
the required start, contiguous covered-through time, request count and completion
state. Inspect held windows without clearing progress or replaying commands.
Progress from another configured controller retains its own opaque identity and
cannot be consumed through the current controller. Provider-unprovable gaps need
deliberate review against retained controller evidence.

Upgrade migrations `20261014_0017` and `20261014_0018` require a separately
authorized deployment and tested pre-upgrade database backup. The first holds
legacy selected Apprise/ambiguous recipient work while preserving definitions,
accepted/uncertain receipts and completed history. Re-select intended destinations
in the workflow editor before deliberately re-enabling affected workflows. An
unrelated edit must not silently bind an old numeric target to a new recipient.
The second adds discovery progress without changing historical candidates. Their
downgrades deliberately refuse to discard review holds or incomplete coverage;
restore the corresponding pre-upgrade backup and matching application version
when rollback is required. Local isolated migration tests do not migrate production.

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

DVSA model labels are shortened to the first word and title-cased for shared
vehicle information, including unknown-arrival notifications: `ZS EXCLUSIVE EV`
becomes `Zs`, `T-ROC LIFE TSI S-A` becomes `T-Roc`, and `TRANSIT CUSTOM 320LMTD
EBLUE A` becomes `Transit`. Known two-word model names, such as Tesla `Model Y`
and Land Rover `Range Rover`, stay together. Make casing is unchanged. Complete
provider model labels remain in the allowlisted provider records; cached and
retained results use the same presentation policy without another provider call.
Existing saved directory models and historical events/notifications are not rewritten.

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

In Edit Vehicle, Admins can use **Refresh vehicle info** beside the registration
to look up make, model, colour and fuel type using the existing coordinated setup
lookup. Returned details replace those fields in the draft; missing values and
edits made while the request is pending are retained. Other drafts, assignments
and MOT/tax information are preserved. Save Changes persists the filled details.
The separate Compliance refresh keeps its confirmed, audited behavior. Both
actions show progress and cannot run together. Provider errors appear beside the
registration, and lookup results still respect shared caching and backoff.

Enabling the integration does not sweep existing vehicles. They refresh on the
next eligible arrival, setup lookup or manual action. Deployment, production
migration, enabling credentials and live provider tests require separate authorization.

## UniFi Protect SDK contract

The locked SDK is `uiprotect==23.1.2`, whose supported console requirement is
[UniFi Protect 7.2+](https://github.com/uilibs/uiprotect#requirements). IACS retains
local username/password and Integration API key credentials for its private
bootstrap/media and public event/device channels. An upgrade does not enable
new hardware actions or replay historical detections.

Event reads use `get_events_raw()` and convert recognized device events into SDK
`Event` models before the existing IACS serializer. Public reads remain capped at
100 results. The browsing adapter accepts at most 250 results internally and one
request asks for at most three times that limit. Explicit device-event types
and disabled SDK manual pagination prevent a whole-window history scan. Camera
filtering stays within that candidate window and may return fewer than the limit;
IACS never fetches more pages to fill it. Unknown/non-device event types are
ignored; a malformed recognized event fails with a sanitized error. Normal SDK
HTTP response allocation precedes this adapter's bounded conversion.

Recovery uses a separate 100-row raw page before filtering, ascending ordering,
explicit smart-detection types and disabled SDK manual pagination. Expanded
millisecond bounds are conservative for either endpoint-inclusion convention.
Captured pages use an explicit safe schema; invalid pages and conflicting
identities hold coverage. Equal timestamps never rely on positional offsets.

Recorded clips use the event's exact cached camera and its start/end times through
`Camera.get_video()`. The SDK retains its media permission check. Missing cameras,
ongoing/invalid ranges, rejected exports and empty responses fail without substituting
a different camera or time range. Normal snapshots retain width/height parameters.
Package snapshots use `get_public_api_camera_snapshot(package=True)` after the
existing local-user live/media permission check and package-camera capability check;
that supported public endpoint returns native quality rather than arbitrary width
and height. Requests above 1920×1080 select high quality.

Private camera detections come from enabled recording/detection settings and the
SDK's supported last-event lookups. Public WebSocket cameras retain the SDK's
current per-type predicates over its cached public events. An ended event is not current evidence;
audio uses its own enabled types independently of the visual detection flag. These
lookups use the SDK's active-event index/cache and perform no provider I/O.

## UniFi optional stream evidence and capacity

UniFi SDK callbacks enter a single consumer with at most 256 retained records and
16 MiB, including its active record. Four fixed probe workers share 64 pending
IDs and a separate 16 MiB budget for pending and active scalar contexts. Finished
IDs and update-publication keys each expire after ten minutes and retain at most
4,096 entries. These are engineering defaults, not production sizing measurements.

The integration status includes capacities, current count/bytes, oldest ages,
coalesced updates, drops and stream gaps. Oversized work is rejected as a whole.
Overflow, failed extraction, disconnect or provider replacement immediately
invalidates cached SDK stream evidence through an inert generation stamp; no
missing update becomes evidence of absence. Fresh observations from the current
provider remain eligible after a gap. The authenticated durable webhook intake
uses its existing independent path.

Probes preserve the original observation receipt and captured timestamps and one
90-second deadline across queue waits and retries. Obsolete or late results cannot
revive current evidence. Shutdown closes admission, detaches the provider under
the service lock, then drains fixed workers and actual client operations and
awaits client close outside that lock. Replacement waits for cleanup. Public
stop/restart rejects calls from its own worker, connection or operation scope
before changing state; application/settings lifecycle callers run externally.

Operation scopes check admission, provider identity and the original deadline
inside the actual child immediately before I/O, then drain started I/O. Stream
and read consumers reject obsolete results. A confirmed Alarm Manager test keeps
its completed provider receipt across concurrent stop/replacement or repeated
caller cancellation, after actual cleanup; a provider rejection remains a failure.
Reads retain normal caller cancellation. Shutdown cannot replace an accepted test
outcome with a stale-generation read error or trigger a retry.

Durable access restart and historical repair retain their stored observation and
original effect deadlines. Exhausted or uncertain work remains held/reconcilable;
recovery does not renew authority or replay a physical command. A restored
database can lack receipts for later physical effects, so a matching source/image
and backup still require uncertainty review before normal workers resume. The
isolated fixture and restore checks do not authorize deployment or live sends.

The idle-worker migration `20261019_0019` and backend must be deployed together
under separate production authorization, with the old worker stopped before upgrade:
old code cannot create the new terminal accounting checkpoint. Preserve a verified
matching pre-upgrade database/image recovery pair. The downgrade deliberately refuses
to discard accounting state; recovery requires that matching restore and uncertainty
review. The migration only backfills missing completion receipts and adds indexes;
it imports no application services and dispatches no work. Once accounting commits,
an authorized audit purge cannot recreate the receipt or increment counters again.

After an authorized deployment, check health and compare three quiet 60-second CPU
and database samples. The operational target is PostgreSQL below 5% of one core and
idle coordinator writes limited to acquisition, release and lease renewal. Investigate
a missed target before declaring the CPU issue resolved; workload and host conditions
can change the result. The later event-driven worker change retains original
execution deadlines, with a 60-second healthy repair check and the old lane-specific
fallback cadence during detected listener outages. See
[background workers](background-workers.md) for the deliberately retained safety timers.

The event-driven index migration and backend are deployed together after separate
authorization, with old workers stopped and a verified matching database/files/auth
and image checkpoint retained. Mixed old/new workers are not a supported cutover:
old producers do not publish the new committed hints. Reconnect/startup catch-up
reads durable queues before relying on notifications. After deployment, compare
three quiet 60-second samples, targeting backend below 1% and PostgreSQL below 0.5%
of one CPU core. Attribute remaining safety work and investigate missed targets;
synthetic or quiet-window results are not a guarantee for other workloads.

Requested mobile snapshots currently have a boolean attachment request and no
explicit optionality contract. A capture failure continues to fail that delivery;
choosing text-only fallback remains a product decision. Existing optional public
snapshot URL omission retains its warning. Voice receipts report provider acceptance,
never proof of audible playback; partial acceptance and uncertainty are visible in
Recent notification delivery history and the run API, with no automatic replay.
