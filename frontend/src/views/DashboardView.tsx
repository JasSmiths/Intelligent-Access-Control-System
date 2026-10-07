import { AlertTriangle, CalendarDays, Car, Check, CheckCircle2, Clock3, Construction, HardHat, ShieldCheck } from "lucide-react";
import React from "react";

import { commandForGate, commandForGarageDoor, insideNowRoster, exitedTodayRoster, personCountLabel, exitCountLabel, profilePhotoForPerson, presenceSegmentWidth, getDashboardEvents, getDashboardAnomalies, formatLongDate, greetingForDate } from "../features/dashboard/model";
import { GateConfirmModal, StatusMetric, GateRow, DoorRow, GarageDoorRow, PresenceStat, PresenceRosterTooltip, ExpectedPresenceTooltip, LegendDot, DashboardEventSnapshotPreview, EventStatusBadge } from "../features/dashboard/OperationalWidgets";
import type { DashboardCommand } from "../features/dashboard/types";
import { useDashboardDirectory } from "../features/dashboard/useDashboardDirectory";
import { useDashboardCommands } from "../features/dashboard/useDashboardCommands";
import { AccessPulse } from "../features/dashboard/AccessPulse";
import { CommandReceiptDetails } from "../features/integrations/CommandReceiptDetails";
import { api, createActionConfirmation } from "../api/client";

import { activeManagedCovers, displayUserName, isActionableAlert, titleCase } from "../lib/format";

import { EmptyState, PanelHeader } from "../ui/primitives";
import type { AccessEvent, Anomaly, ExpectedPresenceSummary, IntegrationStatus, MaintenanceStatus, NavigateToView, Person, Presence, UserAccount, Vehicle } from "../api/types";

import { useModalFocus } from "../ui/useModalFocus";
import { useModalClose } from "../ui/useModalClose";

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
  people: seedPeople,
  vehicles: seedVehicles,
  refreshToken = 0,
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
  refreshToken?: number;
  refresh: () => Promise<void>;
  currentUser: UserAccount;
  navigateToView: NavigateToView;
  onMaintenanceStatusChanged: (status: MaintenanceStatus) => void;
}) {
  const { people, vehicles, activeVehicleCount, error: directoryError } = useDashboardDirectory({
    presence, expectedPresence, events, people: seedPeople, vehicles: seedVehicles, refreshToken
  });
  const [now, setNow] = React.useState(() => new Date());
  const [pendingCommand, setPendingCommand] = React.useState<DashboardCommand | null>(null);
  const [maintenanceDisableOpen, setMaintenanceDisableOpen] = React.useState(false);
  const commandModalRef = React.useRef<HTMLDivElement>(null);
  const maintenanceModalRef = React.useRef<HTMLDivElement>(null);
  const closeCommand = useModalClose(commandModalRef, () => setPendingCommand(null));
  const closeMaintenance = useModalClose(maintenanceModalRef, () => setMaintenanceDisableOpen(false));
  const [maintenanceLoading, setMaintenanceLoading] = React.useState(false);
  const [maintenanceError, setMaintenanceError] = React.useState("");
  const { commandLoading, commandError, setCommandError, savedCommands, receiptReads, setReceiptRefresh,
    receiptStorageError, persistCommands, awaitingResult, runDashboardCommand } = useDashboardCommands({
      currentUser, pendingCommand, maintenanceActive: maintenanceStatus?.is_active === true, closeCommand, refresh
    });
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
  const activeVehicles = activeVehicleCount;
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
      {directoryError ? <p className="auth-error inline-error" role="alert">{directoryError}</p> : null}
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
