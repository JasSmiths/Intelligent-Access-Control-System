import React from "react";
import { DoorClosed, DoorOpen, Warehouse } from "lucide-react";
import { Badge } from "../../ui/primitives";
import { titleCase } from "../../lib/format";
import { useModalFocus } from "../../ui/useModalFocus";
import { useModalClose } from "../../ui/useModalClose";
import type { DoorCommandAction } from "./types";
import { inProgressState, gateStateDisplay, normalizeGateState, ExpectedPresenceTooltipPerson, PresenceRosterPerson, personCountLabel, expectedPresenceTimingLabel, initialsForName, DashboardEvent } from "./model";

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

const PRESENCE_TOOLTIP_LIMIT = 6;

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

export function ExpectedPresenceAvatar({ name, src }: { name: string; src: string | null }) {
  if (src) {
    return <img alt="" className="expected-tooltip-avatar" loading="lazy" src={src} />;
  }
  return <span className="expected-tooltip-avatar fallback">{initialsForName(name)}</span>;
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
