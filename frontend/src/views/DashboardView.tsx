import {
AlertTriangle,
CalendarDays,
Car,
Check,
CheckCircle2,
Clock3,
Construction,
DoorClosed,
DoorOpen,
HardHat,
Lock,
LogOut,
ShieldCheck,
Warehouse
} from "lucide-react";
import React from "react";

import { AccessPulse } from "../features/dashboard/AccessPulse";
import { CommandReceiptDetails } from "../features/integrations/CommandReceiptDetails";
import { api, ApiError, createActionConfirmation } from "../api/client";
import { integrationsApi, coverTargetReceipt, isDeviceCommandReceipt, isGateCommandReceipt, type DeviceCommandReceipt, type GateCommandReceipt } from "../api/integrations";
import { activeManagedCovers, displayUserName, isActionableAlert, titleCase, visitorEventDisplayName } from "../lib/format";
import { mediaSource } from "../lib/media";
import { Badge, EmptyState, PanelHeader } from "../ui/primitives";
import type { AccessEvent, AlertSeverity, Anomaly, ExpectedPresencePerson, ExpectedPresenceSummary, HomeAssistantManagedCover, IntegrationStatus, MaintenanceStatus, NavigateToView, Person, Presence, UserAccount, Vehicle } from "../api/types";
import type { BadgeTone } from "../ui/primitives";
import { useModalFocus } from "../ui/useModalFocus";
import { useModalClose } from "../ui/useModalClose";



export type DoorCommandAction = "open" | "close";

export type DashboardCommand = {
  kind: "gate" | "garage_door";
  entity_id?: string;
  label: string;
  action: DoorCommandAction;
};

type SavedDashboardCommand = { intentId: string; kind: DashboardCommand["kind"]; deviceKey?: string; action: DoorCommandAction };
type DashboardReceipt = GateCommandReceipt | DeviceCommandReceipt;
type ReceiptRead = { receipt?: DashboardReceipt; error?: string; loading?: boolean };

function loadSavedCommands(key: string): SavedDashboardCommand[] {
  try {
    const value: unknown = JSON.parse(window.sessionStorage.getItem(key) || "[]");
    if (!Array.isArray(value)) return [];
    return value.flatMap((item: unknown) => {
      if (!item || typeof item !== "object") return [];
      const row = item as Record<string, unknown>;
      if (typeof row.intentId !== "string" || !row.intentId || (row.kind !== "gate" && row.kind !== "garage_door")
        || (row.action !== "open" && row.action !== "close") || (row.deviceKey !== undefined && typeof row.deviceKey !== "string")) return [];
      return [{ intentId: row.intentId, kind: row.kind, action: row.action, ...(row.deviceKey ? { deviceKey: row.deviceKey } : {}) }];
    });
  } catch { return []; }
}

export function Dashboard(props: Parameters<typeof DashboardSession>[0]) {
  return <DashboardSession key={`${props.currentUser.id}:${props.currentUser.role}`} {...props} />;
}

function DashboardSession({
  presence,
  expectedPresence,
  events,
  anomalies,
  integrationStatus,
  maintenanceStatus,
  people,
  vehicles,
  refresh,
  currentUser,
  navigateToView,
  onMaintenanceStatusChanged
}: {
  presence: Presence[];
  expectedPresence: ExpectedPresenceSummary | null;
  events: AccessEvent[];
  anomalies: Anomaly[];
  integrationStatus: IntegrationStatus | null;
  maintenanceStatus: MaintenanceStatus | null;
  people: Person[];
  vehicles: Vehicle[];
  refresh: () => Promise<void>;
  currentUser: UserAccount;
  navigateToView: NavigateToView;
  onMaintenanceStatusChanged: (status: MaintenanceStatus) => void;
}) {
  const [now, setNow] = React.useState(() => new Date());
  const [pendingCommand, setPendingCommand] = React.useState<DashboardCommand | null>(null);
  const [maintenanceDisableOpen, setMaintenanceDisableOpen] = React.useState(false);
  const commandModalRef = React.useRef<HTMLDivElement>(null);
  const maintenanceModalRef = React.useRef<HTMLDivElement>(null);
  const closeCommand = useModalClose(commandModalRef, () => setPendingCommand(null));
  const closeMaintenance = useModalClose(maintenanceModalRef, () => setMaintenanceDisableOpen(false));
  const [maintenanceLoading, setMaintenanceLoading] = React.useState(false);
  const [maintenanceError, setMaintenanceError] = React.useState("");
  const [commandLoading, setCommandLoading] = React.useState(false);
  const [commandError, setCommandError] = React.useState("");
  const storageKey = `iacs-dashboard-commands:${currentUser.id}:${currentUser.role}`;
  const [savedCommands, setSavedCommands] = React.useState(() => loadSavedCommands(storageKey));
  const [receiptReads, setReceiptReads] = React.useState<Record<string, ReceiptRead>>({});
  const [receiptRefresh, setReceiptRefresh] = React.useState(0);
  const [receiptStorageError, setReceiptStorageError] = React.useState("");
  const commandAttemptRef = React.useRef(false);
  const commandAbortRef = React.useRef<AbortController | null>(null);
  const refreshTimerRef = React.useRef<number | null>(null);
  const lifetimeRef = React.useRef(0);
  React.useLayoutEffect(() => {
    lifetimeRef.current += 1;
    return () => {
      lifetimeRef.current += 1;
      commandAbortRef.current?.abort();
      if (refreshTimerRef.current !== null) window.clearTimeout(refreshTimerRef.current);
    };
  }, []);
  const persistCommands = (commands: SavedDashboardCommand[]) => {
    // Advisory browser index; the durable backend receipt owns the outcome.
    try { window.sessionStorage.setItem(storageKey, JSON.stringify(commands)); }
    catch { setReceiptStorageError("This browser cannot retain action IDs across reloads. Open Command History in Settings to find saved server receipts."); }
    setSavedCommands(commands);
  };
  const [openSnapshotEventId, setOpenSnapshotEventId] = React.useState<string | null>(null);
  const [hoverSnapshotEventId, setHoverSnapshotEventId] = React.useState<string | null>(null);
  const maintenanceActive = maintenanceStatus?.is_active === true;
  const isAdmin = currentUser.role === "admin";
  const exited = presence.filter((item) => item.state === "exited").length;
  const actionableAlerts = anomalies.filter(isActionableAlert);
  const critical = actionableAlerts.filter((item) => item.severity === "critical").length;
  const warning = actionableAlerts.filter((item) => item.severity === "warning").length;
  const displayEvents = getDashboardEvents(events, vehicles, people);
  const displayAnomalies = getDashboardAnomalies(actionableAlerts);
  const expected = expectedPresence?.count ?? 0;
  const peopleById = React.useMemo(
    () => new Map(people.map((person) => [person.id, person])),
    [people]
  );
  const expectedTooltipPeople = React.useMemo(
    () => (expectedPresence?.people ?? []).map((person) => {
      const directoryPerson = peopleById.get(person.person_id);
      return {
        ...person,
        profilePhotoDataUrl: profilePhotoForPerson(directoryPerson)
      };
    }),
    [expectedPresence?.people, peopleById]
  );
  const insideNowPeople = insideNowRoster(presence, peopleById, now);
  const present = insideNowPeople.length;
  const unknown = Math.max(presence.length - present - exited, 0);
  const exitedTodayPeople = exitedTodayRoster(events, vehicles, people, now);
  const activeVehicles = vehicles.filter((vehicle) => vehicle.is_active !== false).length;
  const eventSources = new Set(events.map((event) => event.source).filter(Boolean)).size;

  React.useEffect(() => {
    if (openSnapshotEventId && !displayEvents.some((event) => event.id === openSnapshotEventId)) {
      setOpenSnapshotEventId(null);
    }
  }, [displayEvents, openSnapshotEventId]);


  const gateEntities = activeManagedCovers(integrationStatus?.gate_entities);
  const garageDoorEntities = activeManagedCovers(integrationStatus?.garage_door_entities);
  const topGateState = gateEntities[0]?.state ?? integrationStatus?.current_gate_state ?? integrationStatus?.last_gate_state ?? "unknown";
  const integrationDegraded = Boolean(integrationStatus?.configured && (integrationStatus.degraded || integrationStatus.connected === false));
  const integrationUnavailable = !integrationStatus?.configured;
  const siteStatusTone = maintenanceActive ? "attention" : integrationDegraded || critical ? "degraded" : warning || integrationUnavailable ? "attention" : "normal";
  const siteStatusTitle = maintenanceActive ? "Maintenance Mode Enabled" : integrationDegraded ? "Integration degraded" : critical ? "Critical alerts" : warning ? "Action needed" : integrationUnavailable ? "Device status unavailable" : "No actionable alerts";
  const siteStatusDetail = maintenanceActive ? "All automated actions disabled" : integrationDegraded
    ? integrationStatus?.last_error || "Access-device state sync is not connected"
    : critical
    ? `${critical} critical alert${critical === 1 ? "" : "s"}`
    : warning
      ? `${warning} warning alert${warning === 1 ? "" : "s"}`
      : integrationUnavailable ? "Review integrations for access-device status" : "Your recent alert feed is clear";
  const greeting = greetingForDate(now);
  const firstName = currentUser.first_name || displayUserName(currentUser).split(" ")[0] || "there";

  React.useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  React.useEffect(() => {
    if (!isAdmin || commandLoading || !savedCommands.length) return;
    const controller = new AbortController();
    for (const command of savedCommands) {
      setReceiptReads((reads) => ({ ...reads, [command.intentId]: { ...reads[command.intentId], loading: true } }));
      const request = command.kind === "gate"
        ? integrationsApi.getGateCommandByIntent(command.intentId, { signal: controller.signal })
        : integrationsApi.getCoverCommandByIntent(command.intentId, { signal: controller.signal });
      request.then((receipt) => {
        if (controller.signal.aborted) return;
        if (!(command.kind === "gate" ? isGateCommandReceipt(receipt) : isDeviceCommandReceipt(receipt))) {
          throw new Error("The saved action result was incomplete.");
        }
        setReceiptReads((reads) => ({ ...reads, [command.intentId]: { receipt } }));
      }).catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message = error instanceof ApiError && error.status === 404
          ? "No recorded attempt is available yet. The request may still be processing; this does not establish that it was not sent."
          : error instanceof Error ? error.message : "Unable to read the saved action result.";
        setReceiptReads((reads) => ({ ...reads, [command.intentId]: { receipt: reads[command.intentId]?.receipt, error: message } }));
      });
    }
    return () => controller.abort();
  }, [commandLoading, isAdmin, receiptRefresh, savedCommands]);

  const awaitingResult = (kind: DashboardCommand["kind"], deviceKey?: string) => savedCommands.some((command) => {
    if (command.kind !== kind || (command.deviceKey && deviceKey && command.deviceKey !== deviceKey)) return false;
    const receipt = receiptReads[command.intentId]?.receipt;
    return !receipt || receipt.requires_reconciliation;
  });

  const runDashboardCommand = async () => {
    if (!pendingCommand || commandAttemptRef.current || !isAdmin || maintenanceActive) return;
    const command = pendingCommand;
    commandAttemptRef.current = true;
    setCommandLoading(true);
    setCommandError("");
    const lifetime = lifetimeRef.current;
    const active = () => lifetimeRef.current === lifetime;
    const controller = new AbortController();
    commandAbortRef.current = controller;
    let saved: SavedDashboardCommand | null = null;
    try {
      const reason = `Dashboard ${command.label} ${command.action} command`;
      const gatePayload = { reason, ...(command.entity_id ? { target_device_key: command.entity_id } : {}) };
      const coverPayload = { entity_id: command.entity_id || "", action: command.action, reason };
      const confirmation = command.kind === "gate"
        ? await integrationsApi.confirmGateOpen(gatePayload, command.label)
        : await integrationsApi.confirmCoverCommand(coverPayload, command.label);
      if (!active()) return;
      if (!confirmation.confirmation_id) throw new Error("The action confirmation did not include a recovery ID. No command was requested.");
      saved = { intentId: confirmation.confirmation_id, kind: command.kind, action: command.action, ...(command.entity_id ? { deviceKey: command.entity_id } : {}) };
      persistCommands([...savedCommands.filter((item) => item.intentId !== saved!.intentId), saved]);
      const result = command.kind === "gate"
        ? await integrationsApi.openGate(gatePayload, confirmation.confirmation_token, { signal: controller.signal })
        : await integrationsApi.commandCover(coverPayload, confirmation.confirmation_token, { signal: controller.signal });
      if (!active()) return;
      const receipt = command.kind === "gate" && isGateCommandReceipt(result) ? result : coverTargetReceipt(result);
      setReceiptReads((reads) => ({ ...reads, [saved!.intentId]: receipt
        ? { receipt } : { error: "The response did not include a complete receipt. Check the saved action result before another command." } }));
    } catch (error) {
      if (!active()) return;
      if (saved) {
        const payload: unknown = error instanceof ApiError ? error.payload : null;
        const receipt = command.kind === "gate" && isGateCommandReceipt(payload) ? payload : coverTargetReceipt(payload);
        setReceiptReads((reads) => ({ ...reads, [saved!.intentId]: {
          ...(receipt ? { receipt } : {}),
          error: receipt ? undefined : "The command response was not received. Its delivery is unknown. Check the saved result; do not repeat the command."
        } }));
      } else {
        setCommandError(error instanceof Error ? error.message : "Unable to confirm the action.");
      }
    } finally {
      if (active()) {
        commandAttemptRef.current = false;
        commandAbortRef.current = null;
        setCommandLoading(false);
        if (saved) {
          void closeCommand();
          refresh().catch(() => undefined);
          if (refreshTimerRef.current !== null) window.clearTimeout(refreshTimerRef.current);
          refreshTimerRef.current = window.setTimeout(() => {
            refreshTimerRef.current = null;
            refresh().catch(() => undefined);
          }, 2500);
        }
      }
    }
  };

  const disableMaintenanceMode = async () => {
    if (maintenanceLoading) return;
    setMaintenanceLoading(true);
    setMaintenanceError("");
    try {
      const payload = { reason: "Disabled from Dashboard Site Status icon" };
      const confirmation = await createActionConfirmation("maintenance_mode.disable", payload, {
        target_entity: "MaintenanceMode",
        target_label: "Maintenance Mode",
        reason: payload.reason
      });
      const status = await api.post<MaintenanceStatus>("/api/v1/maintenance/disable", {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      onMaintenanceStatusChanged(status);
      void closeMaintenance();
      await refresh();
    } catch (error) {
      setMaintenanceError(error instanceof Error ? error.message : "Unable to disable Maintenance Mode.");
    } finally {
      setMaintenanceLoading(false);
    }
  };

  return (
    <section className="dashboard-page">
      <div className="dashboard-intro">
        <div>
          <h1>{greeting}, {firstName}</h1>
          <p>Your site at a glance. Presence, access and activity.</p>
        </div>
        <div className="intro-clock">
          <Clock3 size={18} />
          <span>{formatLongDate(now)}</span>
        </div>
      </div>

      <div className="dashboard-grid">
        <div className="card site-status-card">
          <PanelHeader title="Site overview" />
          <div className={`site-status-main ${siteStatusTone}${maintenanceActive ? " maintenance" : ""}`}>
            {maintenanceActive ? (
              <button
                className="maintenance-status-icon"
                disabled={!isAdmin}
                onClick={() => {
                  if (!isAdmin) return;
                  setMaintenanceError("");
                  setMaintenanceDisableOpen(true);
                }}
                type="button"
                aria-label="Disable Maintenance Mode"
              >
                <Construction size={28} strokeWidth={1.6} />
              </button>
            ) : siteStatusTone !== "normal" ? (
              <span className="site-status-icon"><AlertTriangle size={27} strokeWidth={1.7} /></span>
            ) : (
              <span className="site-status-icon"><ShieldCheck size={27} strokeWidth={1.7} /></span>
            )}
            <div>
              <strong>{siteStatusTitle}</strong>
              <span>{siteStatusDetail}</span>
            </div>
          </div>
          <div className="status-metrics">
            <StatusMetric label="People tracked" mobileLabel="People" value={String(people.length)} />
            <StatusMetric label="Active vehicles" mobileLabel="Vehicles" value={String(activeVehicles)} />
            <StatusMetric label="Event sources" mobileLabel="Sources" value={String(eventSources)} />
          </div>
        </div>

        <div className="card gate-card">
          <PanelHeader title="Access points" action="Manage" onAction={isAdmin ? () => navigateToView("settings_gates") : undefined} />
          <div className={maintenanceActive ? "gate-control-section maintenance-disabled" : "gate-control-section"}>
          <div className="gate-list">
            {gateEntities.length ? gateEntities.map((gate) => (
              <GateRow
                icon={Car}
                key={gate.entity_id}
                label={gate.name || "Gate"}
                state={gate.state ?? "unknown"}
                onActionClick={maintenanceActive || !isAdmin || commandLoading || awaitingResult("gate", gate.entity_id) ? undefined : commandForGate(gate.name || "Gate", gate.state ?? "unknown", setPendingCommand, setCommandError, gate.entity_id)}
              />
            )) : (
              <GateRow
                icon={Car}
                label="All configured access gates"
                state={topGateState}
                onActionClick={maintenanceActive || !isAdmin || commandLoading || awaitingResult("gate") ? undefined : commandForGate("All configured access gates", topGateState, setPendingCommand, setCommandError)}
              />
            )}
            {garageDoorEntities.map((door) => (
              <GarageDoorRow
                key={door.entity_id}
                label={door.name || door.entity_id}
                state={door.state ?? "unknown"}
                onActionClick={maintenanceActive || !isAdmin || commandLoading || awaitingResult("garage_door", door.entity_id) ? undefined : commandForGarageDoor(door, setPendingCommand, setCommandError)}
              />
            ))}
            <DoorRow label="Back Door" state={integrationStatus?.back_door_state ?? "unknown"} />
          </div>
          {maintenanceActive ? (
            <div className="maintenance-control-overlay" aria-hidden="true">
              <HardHat size={96} strokeWidth={1} />
            </div>
          ) : null}
          </div>
        </div>

        <div className="card presence-summary-card">
          <PanelHeader title="Presence" action="People" onAction={() => navigateToView("people")} />
          <div className="presence-stats">
            <PresenceStat
              label="Inside Now"
              tooltip={(
                <PresenceRosterTooltip
                  countLabel={personCountLabel(insideNowPeople.length)}
                  emptyLabel="Nobody is inside right now."
                  moreLabel={(hidden) => `+${hidden} more inside`}
                  people={insideNowPeople}
                  title="Inside Now"
                />
              )}
              tooltipLabel="People inside now"
              value={String(present)}
              trend="current"
              tone="green"
            />
            <PresenceStat
              badge={expectedPresence?.learning ? "Learning" : undefined}
              label="Expected"
              tooltip={(
                <ExpectedPresenceTooltip
                  count={expected}
                  learning={expectedPresence?.learning === true}
                  people={expectedTooltipPeople}
                />
              )}
              tooltipLabel="Expected arrivals today"
              value={String(expected)}
              trend="today"
              tone="blue"
            />
            <PresenceStat
              label="Exited Today"
              tooltip={(
                <PresenceRosterTooltip
                  countLabel={exitCountLabel(exitedTodayPeople.length)}
                  emptyLabel="No exits recorded today."
                  moreLabel={(hidden) => `+${hidden} more exits`}
                  people={exitedTodayPeople}
                  title="Exited Today"
                />
              )}
              tooltipLabel="People who exited today"
              value={String(exitedTodayPeople.length)}
              trend="events"
              tone="gray"
            />
          </div>
          <div className="presence-bar" aria-label="Presence mix">
            <span className="residents" style={{ width: `${presenceSegmentWidth(present, presence.length)}%` }} />
            <span className="staff" style={{ width: `${presenceSegmentWidth(exited, presence.length)}%` }} />
            <span className="visitors" style={{ width: `${presenceSegmentWidth(unknown, presence.length)}%` }} />
          </div>
          <div className="presence-legend">
            <LegendDot className="residents" label="Present" value={String(present)} />
            <LegendDot className="staff" label="Exited" value={String(exited)} />
            <LegendDot className="visitors" label="Unknown" value={String(unknown)} />
          </div>
        </div>

        <div className="card recent-events-card">
          <PanelHeader title="Recent Events" action="View all" onAction={() => navigateToView("events")} />
          <div className="event-feed">
            {displayEvents.length ? displayEvents.map((event) => {
              const Icon = event.icon;
              const hasSnapshot = Boolean(event.snapshot_url);
              const snapshotInteractive = hasSnapshot;
              const snapshotOpen = hasSnapshot && (openSnapshotEventId === event.id || hoverSnapshotEventId === event.id);
              const toggleSnapshot = () => setOpenSnapshotEventId((current) => current === event.id ? null : event.id);
              return (
                <div
                  aria-expanded={snapshotInteractive ? snapshotOpen : undefined}
                  aria-label={snapshotInteractive ? `Toggle ${event.snapshotLabel}` : undefined}
                  className={`${hasSnapshot ? "event-feed-row has-snapshot" : "event-feed-row"}${snapshotOpen ? " snapshot-open" : ""}`}
                  key={event.id}
                  onClick={snapshotInteractive ? toggleSnapshot : undefined}
                  onKeyDown={snapshotInteractive ? (keyboardEvent) => {
                    if (keyboardEvent.key === "Enter" || keyboardEvent.key === " ") {
                      keyboardEvent.preventDefault();
                      toggleSnapshot();
                    } else if (keyboardEvent.key === "Escape") {
                      setOpenSnapshotEventId(null);
                      setHoverSnapshotEventId(null);
                    }
                  } : undefined}
                  onPointerEnter={hasSnapshot ? (pointerEvent) => { if (pointerEvent.pointerType === "mouse") setHoverSnapshotEventId(event.id); } : undefined}
                  onPointerLeave={hasSnapshot ? () => setHoverSnapshotEventId(null) : undefined}
                  role={snapshotInteractive ? "button" : undefined}
                  tabIndex={snapshotInteractive ? 0 : undefined}
                >
                  <time>{event.time}</time>
                  <span className={`feed-line ${event.tone}`} />
                  <span className={`event-chip ${event.tone}`}>
                    <Icon size={18} />
                  </span>
                  <div>
                    <strong>{event.label}</strong>
                    <span>{event.subtitle}</span>
                  </div>
                  <EventStatusBadge event={event} />
                  <DashboardEventSnapshotPreview event={event} visible={snapshotOpen} />
                </div>
              );
            }) : <EmptyState icon={CalendarDays} label="No recent events" />}
          </div>
          {displayEvents.length > 0 ? <p className="card-footnote">Latest {displayEvents.length} event{displayEvents.length === 1 ? "" : "s"} from your feed</p> : null}
        </div>

        <div className="card anomaly-card">
          <PanelHeader title="Alerts" action="View all" onAction={() => navigateToView("alerts")} />
          <div className={displayAnomalies.length ? "anomaly-feed" : "anomaly-feed empty"}>
            {displayAnomalies.length ? displayAnomalies.map((item) => (
              <button
                className="anomaly-feed-row"
                key={item.id}
                onClick={() => navigateToView("alerts", { search: `?alert=${encodeURIComponent(item.alertId)}` })}
                type="button"
              >
                <span className={`anomaly-icon ${item.severity}`}>
                  <AlertTriangle size={20} />
                </span>
                <div>
                  <strong>{item.title}</strong>
                  <span>{item.detail}</span>
                </div>
                <time>{item.time}</time>
              </button>
            )) : <div className="dashboard-clear-state"><CheckCircle2 size={28} strokeWidth={1.5} /><strong>No actionable alerts</strong><p>New alerts that need your attention will appear here.</p></div>}
          </div>
          {actionableAlerts.length ? <p className="unresolved-count">{actionableAlerts.length} action needed</p> : null}
        </div>

        <div className="card chart-card">
          <AccessPulse events={events} now={now} onOpenEvents={() => navigateToView("events")} />
        </div>
      </div>

      {isAdmin && savedCommands.length ? (
        <section className="card" aria-label="Saved gate and garage actions">
          <PanelHeader title="Gate and garage action results" />
          {receiptStorageError ? <p role="alert">{receiptStorageError}</p> : null}
          {savedCommands.map((command) => {
            const read = receiptReads[command.intentId];
            const label = [...gateEntities, ...garageDoorEntities].find((device) => device.entity_id === command.deviceKey)?.name
              || command.deviceKey || "All configured access gates";
            return <section key={command.intentId} aria-label={`${titleCase(command.action)} ${label} result`}>
              <h3>{titleCase(command.action)} {label}</h3>
              {read?.receipt ? <CommandReceiptDetails receipt={read.receipt} /> : <p>Delivery unknown — awaiting a saved action result. Do not repeat the command.</p>}
              {read?.error ? <p role="alert">{read.error}</p> : null}
              <button className="secondary-button" type="button" disabled={commandLoading || read?.loading} onClick={() => setReceiptRefresh((value) => value + 1)}>{read?.loading ? "Checking result…" : "Check action result"}</button>
              {read?.receipt && !read.receipt.requires_reconciliation ? <button className="secondary-button" type="button" onClick={() => persistCommands(savedCommands.filter((item) => item.intentId !== command.intentId))}>Dismiss result</button> : null}
            </section>;
          })}
        </section>
      ) : null}

      {pendingCommand ? (
        <GateConfirmModal
          modalRef={commandModalRef}
          action={pendingCommand.action}
          error={commandError}
          label={pendingCommand.label}
          loading={commandLoading}
          onCancel={() => {
            if (commandLoading) return;
            setPendingCommand(null);
            setCommandError("");
          }}
          onConfirm={runDashboardCommand}
        />
      ) : null}
      {maintenanceDisableOpen ? (
        <MaintenanceDisableModal
          modalRef={maintenanceModalRef}
          error={maintenanceError}
          loading={maintenanceLoading}
          onCancel={() => {
            if (maintenanceLoading) return;
            setMaintenanceDisableOpen(false);
            setMaintenanceError("");
          }}
          onConfirm={disableMaintenanceMode}
        />
      ) : null}
    </section>
  );
}

export function MaintenanceDisableModal({
  error,
  loading,
  modalRef: providedRef,
  onCancel: finishCancel,
  onConfirm
}: {
  error: string;
  loading: boolean;
  modalRef?: React.RefObject<HTMLDivElement | null>;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const localRef = React.useRef<HTMLDivElement>(null);
  const modalRef = providedRef ?? localRef;
  const onCancel = useModalClose(modalRef, finishCancel);
  useModalFocus(modalRef, true, () => { if (!loading) onCancel(); });
  return (
    <div className="modal-backdrop" role="presentation">
      <div ref={modalRef} className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="maintenance-disable-title">
        <div className="modal-header">
          <div className="gate-confirm-title">
            <span className="gate-confirm-icon maintenance">
              <Construction size={20} strokeWidth={1} />
            </span>
            <div>
              <h2 className="maintenance-disable-title" id="maintenance-disable-title">Disable Maintenance Mode</h2>
              <p>Allow automated actions to resume normal operation</p>
            </div>
          </div>
        </div>
        {error ? <div className="auth-error inline-error">{error}</div> : null}
        <div className="modal-actions">
          <button className="secondary-button" disabled={loading} onClick={onCancel} type="button">
            Cancel
          </button>
          <button className="primary-button" disabled={loading} onClick={onConfirm} type="button">
            <Check size={16} />
            {loading ? "Resuming..." : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function GateConfirmModal({
  action,
  error,
  label,
  loading,
  modalRef: providedRef,
  onCancel: finishCancel,
  onConfirm
}: {
  action: DoorCommandAction;
  error: string;
  label: string;
  loading: boolean;
  modalRef?: React.RefObject<HTMLDivElement | null>;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const localRef = React.useRef<HTMLDivElement>(null);
  const modalRef = providedRef ?? localRef;
  const onCancel = useModalClose(modalRef, finishCancel);
  const dismiss = () => {
    if (!loading) return onCancel();
  };
  useModalFocus(modalRef, true, dismiss);
  const actionLabel = titleCase(action);
  const isGarage = label.toLowerCase().includes("garage");
  const Icon = isGarage
    ? Warehouse
    : action === "open" ? DoorOpen : DoorClosed;
  return (
    <div className="modal-backdrop" role="presentation">
      <div ref={modalRef} className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="gate-confirm-title">
        <div className="modal-header">
          <div className="gate-confirm-title">
            <span className="gate-confirm-icon">
              <Icon size={20} />
            </span>
            <div>
              <h2 id="gate-confirm-title">{actionLabel} {label}?</h2>
            </div>
          </div>
        </div>
        {error ? <div className="auth-error inline-error">{error}</div> : null}
        <div className="modal-actions">
          <button className="secondary-button" disabled={loading} onClick={dismiss} type="button">
            Cancel
          </button>
          <button className="primary-button" disabled={loading} onClick={onConfirm} type="button">
            <Icon size={16} />
            {loading ? `${titleCase(inProgressState(action))}...` : `${actionLabel} ${label}`}
          </button>
        </div>
      </div>
    </div>
  );
}

export function StatusMetric({ label, mobileLabel, value }: { label: string; mobileLabel?: string; value: string }) {
  return (
    <div>
      <span>
        <span className="status-label status-label-desktop">{label}</span>
        <span className="status-label status-label-mobile">{mobileLabel ?? label}</span>
      </span>
      <strong>{value}</strong>
    </div>
  );
}

export function GateRow({
  icon: Icon,
  label,
  state,
  onActionClick
}: {
  icon: React.ElementType;
  label: string;
  state: string;
  onActionClick?: () => void;
}) {
  const normalized = normalizeGateState(state);
  const display = gateStateDisplay(state);
  const hasAction = (normalized === "closed" || normalized === "open") && display.actionable && onActionClick;
  return (
    <div className="gate-row">
      <Icon size={18} />
      <strong>{label}</strong>
      {hasAction ? (
        <button className={`badge ${display.tone} badge-action`} onClick={onActionClick} type="button">
          {display.label}
        </button>
      ) : (
        <Badge tone={display.tone}>{display.label}</Badge>
      )}
    </div>
  );
}

export function DoorRow({ label, state }: { label: string; state: string }) {
  const normalized = normalizeGateState(state);
  const Icon = normalized === "open" ? DoorOpen : DoorClosed;
  return <GateRow icon={Icon} label={label} state={state} />;
}

export function GarageDoorRow({ label, state, onActionClick }: { label: string; state: string; onActionClick?: () => void }) {
  return <GateRow icon={Warehouse} label={label} state={state} onActionClick={onActionClick} />;
}

export function commandForGate(
  label: string,
  state: string,
  setPendingCommand: React.Dispatch<React.SetStateAction<DashboardCommand | null>>,
  setCommandError: React.Dispatch<React.SetStateAction<string>>,
  deviceKey?: string
) {
  const normalized = normalizeGateState(state);
  if (normalized !== "closed") return undefined;
  return () => {
    setCommandError("");
    setPendingCommand({ kind: "gate", label, action: "open", ...(deviceKey ? { entity_id: deviceKey } : {}) });
  };
}

export function commandForGarageDoor(
  door: HomeAssistantManagedCover,
  setPendingCommand: React.Dispatch<React.SetStateAction<DashboardCommand | null>>,
  setCommandError: React.Dispatch<React.SetStateAction<string>>
) {
  const normalized = normalizeGateState(door.state ?? "unknown");
  if (!["open", "closed"].includes(normalized)) return undefined;
  const action = normalized === "open" ? "close" : "open";
  return () => {
    setCommandError("");
    setPendingCommand({ kind: "garage_door", entity_id: door.entity_id, label: door.name || door.entity_id, action });
  };
}

export function inProgressState(action: DoorCommandAction) {
  return action === "open" ? "opening" : "closing";
}

export function gateStateDisplay(state: string): { label: string; tone: BadgeTone; actionable: boolean } {
  const normalized = state.toLowerCase();
  if (normalized === "open") return { label: "Open", tone: "green", actionable: true };
  if (normalized === "opening") return { label: "Opening", tone: "amber", actionable: false };
  if (normalized === "closed") return { label: "Closed", tone: "gray", actionable: true };
  if (normalized === "closing") return { label: "Closing", tone: "amber", actionable: false };
  return { label: "Unknown", tone: "amber", actionable: false };
}

export function normalizeGateState(state: string) {
  const normalized = state.toLowerCase();
  if (["open", "opening"].includes(normalized)) return "open";
  if (["closed", "closing"].includes(normalized)) return "closed";
  return "unknown";
}

export function PresenceStat({
  badge,
  label,
  tooltip,
  tooltipLabel,
  value,
  trend,
  tone
}: {
  badge?: string;
  label: string;
  tooltip?: React.ReactNode;
  tooltipLabel?: string;
  value: string;
  trend: string;
  tone: "green" | "blue" | "gray";
}) {
  const tooltipId = React.useId();
  const [tooltipOpen, setTooltipOpen] = React.useState(false);

  return (
    <div
      aria-describedby={tooltip ? tooltipId : undefined}
      aria-label={tooltip ? tooltipLabel : undefined}
      className={`presence-stat${tooltip ? " has-tooltip" : ""}${tooltipOpen ? " tooltip-open" : ""}`}
      onBlur={tooltip ? () => setTooltipOpen(false) : undefined}
      onClick={tooltip ? () => setTooltipOpen(true) : undefined}
      onKeyDown={tooltip ? (event) => {
        if (event.key === "Escape") setTooltipOpen(false);
      } : undefined}
      onMouseLeave={tooltip ? () => setTooltipOpen(false) : undefined}
      tabIndex={tooltip ? 0 : undefined}
    >
      {badge ? <em className="presence-stat-pill">{badge}</em> : null}
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
      <small>{trend}</small>
      {tooltip ? (
        <div
          className="iacs-tooltip expected-presence-tooltip bottom"
          id={tooltipId}
          role="tooltip"
        >
          {tooltip}
        </div>
      ) : null}
    </div>
  );
}

type ExpectedPresenceTooltipPerson = ExpectedPresencePerson & {
  profilePhotoDataUrl: string | null;
};

const PRESENCE_TOOLTIP_LIMIT = 6;

export type PresenceRosterPerson = {
  id: string;
  display_name: string;
  profilePhotoDataUrl: string | null;
  detail: string;
};

export function PresenceRosterTooltip({
  badge,
  countLabel,
  emptyLabel,
  moreLabel,
  people,
  title
}: {
  badge?: string;
  countLabel: string;
  emptyLabel: string;
  moreLabel: (hidden: number) => string;
  people: PresenceRosterPerson[];
  title: string;
}) {
  const visible = people.slice(0, PRESENCE_TOOLTIP_LIMIT);
  const hidden = people.length - visible.length;
  return (
    <>
      <div className="expected-tooltip-head">
        <div>
          <strong>{title}</strong>
          <span>{countLabel}</span>
        </div>
        {badge ? <em>{badge}</em> : null}
      </div>
      {people.length ? (
        <div className="expected-tooltip-list">
          {visible.map((person) => (
            <div className="expected-tooltip-person" key={person.id}>
              <ExpectedPresenceAvatar name={person.display_name} src={person.profilePhotoDataUrl} />
              <div>
                <strong>{person.display_name}</strong>
                <span>{person.detail}</span>
              </div>
            </div>
          ))}
          {hidden > 0 ? <span className="expected-tooltip-more">{moreLabel(hidden)}</span> : null}
        </div>
      ) : (
        <span className="expected-tooltip-empty">{emptyLabel}</span>
      )}
    </>
  );
}

export function ExpectedPresenceTooltip({
  count,
  learning,
  people
}: {
  count: number;
  learning: boolean;
  people: ExpectedPresenceTooltipPerson[];
}) {
  return (
    <PresenceRosterTooltip
      badge={learning ? "Learning" : undefined}
      countLabel={personCountLabel(count)}
      emptyLabel="No expected arrivals learned for today yet."
      moreLabel={(hidden) => `+${hidden} more expected`}
      people={people.map((person) => ({
        id: person.person_id,
        display_name: person.display_name,
        profilePhotoDataUrl: person.profilePhotoDataUrl,
        detail: expectedPresenceTimingLabel(person)
      }))}
      title="Expected Today"
    />
  );
}

export function insideNowRoster(rows: Presence[], peopleById: Map<string, Person>, now = new Date()): PresenceRosterPerson[] {
  return rows
    .filter((row) => row.state === "present")
    .slice()
    .sort((left, right) => {
      const byTime = timeValue(right.last_changed_at) - timeValue(left.last_changed_at);
      return byTime || left.display_name.localeCompare(right.display_name);
    })
    .map((row) => ({
      id: row.person_id,
      display_name: row.display_name,
      profilePhotoDataUrl: profilePhotoForPerson(peopleById.get(row.person_id)),
      detail: insideSinceLabel(row.last_changed_at, now)
    }));
}

export function exitedTodayRoster(events: AccessEvent[], vehicles: Vehicle[], people: Person[], now = new Date()): PresenceRosterPerson[] {
  const peopleById = new Map(people.map((person) => [person.id, person]));
  const vehiclesByRegistration = new Map(vehicles.map((vehicle) => [vehicle.registration_number.toUpperCase(), vehicle]));
  return events
    .filter((event) => event.direction === "exit" && isToday(event.occurred_at, now))
    .slice()
    .sort((left, right) => timeValue(right.occurred_at) - timeValue(left.occurred_at) || left.id.localeCompare(right.id))
    .map((event) => exitRosterPerson(event, vehiclesByRegistration.get(event.registration_number.toUpperCase()), people, peopleById));
}

function exitRosterPerson(
  event: AccessEvent,
  vehicle: Vehicle | undefined,
  people: Person[],
  peopleById: Map<string, Person>
): PresenceRosterPerson {
  const time = formatTime(event.occurred_at);
  const visitorName = visitorEventDisplayName(event);
  if (visitorName) {
    return { id: event.id, display_name: visitorName, profilePhotoDataUrl: null, detail: `Exited at ${time}` };
  }
  const owners = ownersForExit(event.registration_number, vehicle, people, peopleById);
  if (owners.length) {
    return {
      id: event.id,
      display_name: owners.map((person) => person.display_name).join(", "),
      profilePhotoDataUrl: owners.length === 1 ? profilePhotoForPerson(owners[0]) : null,
      detail: `Exited at ${time}`
    };
  }
  const ownerLabel = vehicle?.owners?.filter(Boolean).join(", ") || vehicle?.owner || "";
  if (ownerLabel) {
    return { id: event.id, display_name: ownerLabel, profilePhotoDataUrl: null, detail: `Exited at ${time}` };
  }
  return {
    id: event.id,
    display_name: "Unknown",
    profilePhotoDataUrl: null,
    detail: event.registration_number ? `${event.registration_number} · ${time}` : `Exited at ${time}`
  };
}

function ownersForExit(registration: string, vehicle: Vehicle | undefined, people: Person[], peopleById: Map<string, Person>) {
  const owners = new Map<string, Person>();
  const add = (person: Person | undefined) => {
    if (person) owners.set(person.id, person);
  };
  for (const personId of vehicle?.person_ids ?? []) add(peopleById.get(personId));
  if (vehicle?.person_id) add(peopleById.get(vehicle.person_id));
  const key = registration.toUpperCase();
  for (const person of people) {
    if ((person.vehicles ?? []).some((item) => item.registration_number.toUpperCase() === key)) add(person);
  }
  return [...owners.values()].sort((left, right) => left.display_name.localeCompare(right.display_name));
}

export function insideSinceLabel(value: string | null, now: Date) {
  if (!value) return "Currently inside";
  if (isToday(value, now)) return `Since ${formatTime(value)}`;
  const date = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(new Date(value));
  return `Since ${date}`;
}

function personCountLabel(count: number) {
  return `${count} ${count === 1 ? "person" : "people"}`;
}

function exitCountLabel(count: number) {
  return `${count} ${count === 1 ? "exit" : "exits"}`;
}

function profilePhotoForPerson(person: Person | undefined) {
  if (!person) return null;
  return mediaSource(person.profile_photo_url, person.profile_photo_data_url, "thumb") || null;
}

function timeValue(value: string | null) {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function ExpectedPresenceAvatar({ name, src }: { name: string; src: string | null }) {
  if (src) {
    return <img alt="" className="expected-tooltip-avatar" loading="lazy" src={src} />;
  }
  return <span className="expected-tooltip-avatar fallback">{initialsForName(name)}</span>;
}

export function expectedPresenceTimingLabel(person: ExpectedPresenceTooltipPerson) {
  if (person.evidence_days === 0) {
    return person.typical_arrival ? `Seen today at ${person.typical_arrival}` : "Seen today";
  }
  if (person.typical_arrival) {
    return `Usually ${person.typical_arrival}`;
  }
  return `${person.evidence_days} routine ${person.evidence_days === 1 ? "day" : "days"}`;
}

export function initialsForName(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return parts.slice(0, 2).map((part) => part[0]?.toUpperCase() ?? "").join("");
}

export function presenceSegmentWidth(value: number, total: number) {
  if (!total || !value) return 0;
  return Math.max((value / total) * 100, 6);
}

export function LegendDot({ className, label, value }: { className: string; label: string; value: string }) {
  return (
    <span>
      <i className={className} />
      {label}
      <strong>{value}</strong>
    </span>
  );
}

export type DashboardEvent = {
  id: string;
  time: string;
  label: string;
  subtitle: string;
  snapshot_url: string | null;
  snapshotLabel: string;
  status: "IN" | "OUT";
  statusTone: BadgeTone;
  statusIcon?: React.ElementType;
  statusLabel: string;
  tone: "green" | "blue" | "gray" | "amber";
  icon: React.ElementType;
};

export function getDashboardEvents(events: AccessEvent[], vehicles: Vehicle[], people: Person[]): DashboardEvent[] {
  const peopleById = new Map(people.map((person) => [person.id, person]));
  const vehiclesByRegistration = new Map(vehicles.map((vehicle) => [vehicle.registration_number.toUpperCase(), vehicle]));

  return events.slice(0, 5).map((event) => {
    const vehicle = vehiclesByRegistration.get(event.registration_number.toUpperCase());
    const owner = vehicle?.person_id ? peopleById.get(vehicle.person_id) : undefined;
    const ownerFirstName = owner?.first_name || vehicle?.owner?.split(" ")[0] || "";
    const visitorName = visitorEventDisplayName(event);
    const isDenied = event.decision === "denied" || event.direction === "denied";

    return {
      id: event.id,
      time: formatTime(event.occurred_at),
      label: visitorName || ownerFirstName || "Unknown",
      subtitle: `${event.registration_number}  •  ${event.visitor_pass_id ? "Visitor Pass" : "LPR"}`,
      snapshot_url: event.snapshot_url,
      snapshotLabel: `Snapshot for ${visitorName || ownerFirstName || event.registration_number}`,
      status: event.direction === "exit" ? "OUT" : "IN",
      statusTone: isDenied ? "amber" : event.direction === "entry" ? "green" : "gray",
      statusIcon: isDenied ? Lock : undefined,
      statusLabel: isDenied ? "Denied" : event.direction === "exit" ? "Out" : "In",
      tone: isDenied ? "amber" : event.direction === "entry" ? "green" : "blue",
      icon: event.direction === "exit" ? LogOut : isDenied ? AlertTriangle : Car
    };
  });
}

export function DashboardEventSnapshotPreview({ event, visible }: { event: DashboardEvent; visible: boolean }) {
  const [hasBeenVisible, setHasBeenVisible] = React.useState(false);
  React.useEffect(() => { if (visible) setHasBeenVisible(true); }, [visible]);
  if (!event.snapshot_url) return null;
  return (
    <span className="dashboard-event-snapshot-preview" aria-hidden={!visible}>
      <span className="dashboard-event-snapshot-clip">
        <span className="dashboard-event-snapshot-frame">
          {(visible || hasBeenVisible) && <img alt={event.snapshotLabel} decoding="async" loading="lazy" src={event.snapshot_url} />}
        </span>
      </span>
    </span>
  );
}

export function EventStatusBadge({ event }: { event: DashboardEvent }) {
  if (event.statusIcon) {
    const Icon = event.statusIcon;
    return (
      <Badge tone={event.statusTone}>
        <span className="event-status-icon" aria-label={event.statusLabel} title={event.statusLabel}>
          <Icon size={13} aria-hidden="true" />
        </span>
      </Badge>
    );
  }
  return <Badge tone={event.statusTone}>{event.status}</Badge>;
}

export type DashboardAnomaly = {
  id: string;
  alertId: string;
  title: string;
  detail: string;
  time: string;
  severity: AlertSeverity;
};

export function getDashboardAnomalies(anomalies: Anomaly[]): DashboardAnomaly[] {
  return anomalies.slice(0, 4).map((item) => ({
    id: item.id,
    alertId: item.grouped ? item.alert_ids[0] : item.id,
    title: titleCase(item.type),
    detail: item.message,
    time: formatTime(item.last_seen_at || item.created_at),
    severity: item.severity
  }));
}

export function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true
  }).format(new Date(value));
}

export function formatLongDate(value: Date) {
  const date = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric"
  }).format(value);
  const time = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true
  }).format(value);
  return `${date} • ${time}`;
}

export function greetingForDate(value: Date) {
  const hour = value.getHours();
  if (hour < 12) return "Good Morning";
  if (hour < 17) return "Good Afternoon";
  if (hour < 22) return "Good Evening";
  return "Good Night";
}

export function isToday(value: string, now = new Date()) {
  const date = new Date(value);
  return (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  );
}
