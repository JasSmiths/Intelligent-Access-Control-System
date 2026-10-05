import { Car, CheckCircle2, CircleDot, ClipboardPaste, Clock3, GitBranch, Ticket, X } from "lucide-react";
import React from "react";

import { formatDate, titleCase } from "../../lib/format";



import type { VisitorPassStatus, VisitorPass } from "./types";
import { visitorPassStatuses, defaultVisitorPassFilters, visitorPassBaseStatusTone, visitorPassStatusPillTone, visitorPassWindowLabel, visitorPassSourceLabel, visitorPassInitials, visitorPassPassDurationLabel, visitorPassVisitDurationLabel, visitorPassVehicleSummary } from "./model";

export function PassFilterBar({
  filters,
  counts,
  onChange
}: {
  filters: Set<VisitorPassStatus>;
  counts: Record<VisitorPassStatus, number>;
  onChange: (filters: Set<VisitorPassStatus>) => void;
}) {
  const allSelected = filters.size === visitorPassStatuses.length;
  const toggleStatus = (status: VisitorPassStatus) => {
    const next = new Set(filters);
    if (next.has(status)) {
      next.delete(status);
    } else {
      next.add(status);
    }
    onChange(next.size ? next : new Set(defaultVisitorPassFilters));
  };
  return (
    <div className="pass-filter-bar" role="group" aria-label="Visitor Pass status filters">
      <button className={allSelected ? "active" : ""} onClick={() => onChange(new Set(visitorPassStatuses))} type="button">
        All
      </button>
      {visitorPassStatuses.map((status) => (
        <button
          aria-pressed={filters.has(status)}
          className={filters.has(status) ? "active" : ""}
          key={status}
          onClick={() => toggleStatus(status)}
          type="button"
        >
          {titleCase(status)}
          <span>{counts[status]}</span>
        </button>
      ))}
    </div>
  );
}

export function VisitorPassCard({
  visitorPass,
  onOpen
}: {
  visitorPass: VisitorPass;
  onOpen: (visitorPass: VisitorPass) => void;
}) {
  const vehicleSummary = visitorPassVehicleSummary(visitorPass);
  const windowLabel = visitorPassWindowLabel(visitorPass);
  const sourceLabel = visitorPassSourceLabel(visitorPass.creation_source);
  const isDuration = visitorPass.pass_type === "duration";
  const visitDuration = visitorPassVisitDurationLabel(visitorPass);
  const passDuration = visitorPassPassDurationLabel(visitorPass);
  const subtitle = isDuration ? formatDate(visitorPass.window_start) : `${formatDate(visitorPass.window_start)} · ${windowLabel}`;
  const vehicleMeta = [visitorPass.vehicle_colour, visitorPass.vehicle_make].filter(Boolean).join(" ");
  const vehiclePrimary = visitorPass.number_plate || vehicleSummary || "Pending";
  const vehicleSecondary = vehicleMeta || "Vehicle";
  return (
    <article
      className={`card visitor-pass-card ${visitorPass.status}`}
      onClick={() => onOpen(visitorPass)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(visitorPass);
        }
      }}
      role="button"
      tabIndex={0}
    >
      <div className="visitor-pass-card-head">
        <div className="visitor-pass-icon">
          <Ticket size={18} />
        </div>
        <div>
          <strong>{visitorPass.visitor_name}</strong>
          <span className="visitor-pass-card-subtitle">{subtitle}</span>
        </div>
        <VisitorPassStatusPill showIcon={false} status={visitorPass.status} visitorPass={visitorPass} />
      </div>

      <div className="visitor-pass-window">
        <div className="visitor-pass-window-row">
          <Clock3 size={15} />
          <span>{formatDate(visitorPass.window_start)} to {formatDate(visitorPass.window_end)}</span>
        </div>
        <div className="visitor-pass-window-row">
          <GitBranch size={15} />
          <span>
            {sourceLabel}
            {visitorPass.visitor_phone ? ` · +${visitorPass.visitor_phone}` : visitorPass.created_by ? ` · ${visitorPass.created_by}` : ""}
          </span>
        </div>
      </div>

      <section className="visitor-pass-card-stats">
        <div className="visitor-pass-card-stat">
          <span className="visitor-pass-stat-icon vehicle">
            <Car size={19} />
          </span>
          <div>
            <strong>{vehiclePrimary}</strong>
            <span>{vehicleSecondary}</span>
          </div>
        </div>
        <div className="visitor-pass-card-stat">
          <span className="visitor-pass-stat-icon duration">
            <Clock3 size={19} />
          </span>
          <div>
            <strong>{visitDuration || passDuration || "Pending"}</strong>
            <span>Duration</span>
          </div>
        </div>
      </section>
    </article>
  );
}

export function VisitorPassStatusPill({
  status,
  visitorPass,
  showIcon = true
}: {
  status: VisitorPassStatus;
  visitorPass?: VisitorPass;
  showIcon?: boolean;
}) {
  const Icon = status === "scheduled" || status === "active" ? Clock3 : status === "used" ? CheckCircle2 : status === "cancelled" ? X : CircleDot;
  const tone = visitorPass ? visitorPassStatusPillTone(visitorPass) : visitorPassBaseStatusTone(status);
  return (
    <span className={`visitor-pass-status-pill tone-${tone}`}>
      {showIcon ? <Icon size={18} /> : null}
      <span className="visitor-pass-status-label">{titleCase(status)}</span>
    </span>
  );
}

export function VisitorPassAvatar({ visitorPass }: { visitorPass: VisitorPass }) {
  const initials = visitorPassInitials(visitorPass.visitor_name);
  return (
    <span className="visitor-pass-avatar" aria-hidden="true">
      {initials || <ClipboardPaste size={24} />}
    </span>
  );
}

export function VisitorPassDetailTile({
  icon: Icon,
  tone,
  label,
  value,
  detail
}: {
  icon: React.ElementType;
  tone: "blue" | "green" | "amber" | "purple";
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className={`visitor-pass-detail-tile ${tone}`}>
      <span className="visitor-pass-detail-tile-icon">
        <Icon size={24} />
      </span>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </div>
  );
}
