import React from "react";
import { createPortal } from "react-dom";
import { Camera } from "lucide-react";
import type { TooltipPositionState } from "../../api/types";
import type { ReportDurationInfo, ReportSnapshotEvent } from "../../api/reports";
import { getUsableViewportBounds, observeOverlayPlacement, placeOverlay } from "../../lib/viewportPlacement";
import { formatSiteDate } from "./model";

export function ReportDurationCell({ duration }: { duration: ReportDurationInfo }) {
  const tooltipId = React.useId();
  const [tooltipPosition, setTooltipPosition] = React.useState<TooltipPositionState | null>(null);
  const targetRef = React.useRef<HTMLElement | null>(null);
  const tooltipRef = React.useRef<HTMLDivElement | null>(null);

  React.useLayoutEffect(() => {
    if (!tooltipPosition) return undefined;
    const update = () => {
      const target = targetRef.current;
      const tooltip = tooltipRef.current;
      if (!target || !tooltip) return;
      const placement = placeOverlay(target.getBoundingClientRect(), { width: tooltip.offsetWidth, height: tooltip.scrollHeight }, getUsableViewportBounds(), { gap: 10, alignment: "center" });
      const center = placement.left + Math.min(tooltip.offsetWidth, placement.maxWidth) / 2;
      setTooltipPosition((current) => current && (current.left !== center || current.top !== placement.top || current.placement !== placement.side)
        ? { left: center, top: placement.top, placement: placement.side }
        : current);
      tooltip.style.maxWidth = `${placement.maxWidth}px`;
      tooltip.style.maxHeight = `${placement.maxHeight}px`;
      tooltip.style.overflow = "auto";
      tooltip.style.pointerEvents = "auto";
      tooltip.style.visibility = "visible";
    };
    return observeOverlayPlacement(targetRef.current, tooltipRef.current, update);
  }, [Boolean(tooltipPosition)]);

  const showTooltip = (target: HTMLElement) => {
    if (!duration.tooltip) return;
    targetRef.current = target;
    setTooltipPosition({ left: 0, top: 0, placement: "bottom" });
  };

  if (!duration.tooltip) {
    return <span className={`report-duration-value ${duration.tone ?? ""}`}>{duration.label}</span>;
  }

  return (
    <button
      aria-describedby={tooltipPosition ? tooltipId : undefined}
      className="report-duration-value interactive"
      onBlur={() => setTooltipPosition(null)}
      onFocus={(event) => showTooltip(event.currentTarget)}
      onKeyDown={(event) => {
        if (event.key === "Escape") setTooltipPosition(null);
      }}
      onMouseEnter={(event) => showTooltip(event.currentTarget)}
      onMouseLeave={(event) => { if (document.activeElement !== event.currentTarget) setTooltipPosition(null); }}
      type="button"
    >
      {duration.label}
      {tooltipPosition ? createPortal(
        <div
          className={`iacs-tooltip report-duration-tooltip ${tooltipPosition.placement}`}
          id={tooltipId}
          ref={tooltipRef}
          role="tooltip"
          style={{ left: tooltipPosition.left, top: tooltipPosition.top, visibility: tooltipPosition.left === 0 ? "hidden" : "visible" }}
        >
          <strong>{duration.tooltip}</strong>
          {duration.tooltipDetail ? <span>{duration.tooltipDetail}</span> : null}
        </div>,
        document.body
      ) : null}
    </button>
  );
}

export function ReportSnapshotThumb({ event, timezone }: { event: ReportSnapshotEvent; timezone: string }) {
  const tooltipId = React.useId();
  const [tooltipPosition, setTooltipPosition] = React.useState<TooltipPositionState | null>(null);
  const targetRef = React.useRef<HTMLElement | null>(null);
  const tooltipRef = React.useRef<HTMLDivElement | null>(null);

  React.useLayoutEffect(() => {
    if (!tooltipPosition) return undefined;
    const update = () => {
      const target = targetRef.current;
      const tooltip = tooltipRef.current;
      if (!target || !tooltip) return;
      const placement = placeOverlay(target.getBoundingClientRect(), { width: tooltip.offsetWidth, height: tooltip.scrollHeight }, getUsableViewportBounds(), { gap: 10, alignment: "center" });
      const center = placement.left + Math.min(tooltip.offsetWidth, placement.maxWidth) / 2;
      setTooltipPosition((current) => current && (current.left !== center || current.top !== placement.top || current.placement !== placement.side)
        ? { left: center, top: placement.top, placement: placement.side }
        : current);
      tooltip.style.maxWidth = `${placement.maxWidth}px`;
      tooltip.style.maxHeight = `${placement.maxHeight}px`;
      tooltip.style.overflow = "auto";
      tooltip.style.pointerEvents = "auto";
      tooltip.style.visibility = "visible";
    };
    return observeOverlayPlacement(targetRef.current, tooltipRef.current, update);

  }, [Boolean(tooltipPosition)]);

  const showTooltip = (target: HTMLElement) => {
    if (!event.snapshot_url) return;
    targetRef.current = target;
    setTooltipPosition({ left: 0, top: 0, placement: "bottom" });
  };

  if (!event.snapshot_url) {
    return (
      <span className="report-table-snapshot empty" aria-label="No snapshot available">
        <Camera size={14} />
      </span>
    );
  }

  return (
    <button
      aria-describedby={tooltipPosition ? tooltipId : undefined}
      aria-label={`Snapshot for ${event.registration_number}`}
      className="report-table-snapshot thumb"
      onBlur={() => setTooltipPosition(null)}
      onFocus={(mouseEvent) => showTooltip(mouseEvent.currentTarget)}
      onKeyDown={(keyboardEvent) => {
        if (keyboardEvent.key === "Escape") setTooltipPosition(null);
      }}
      onMouseEnter={(mouseEvent) => showTooltip(mouseEvent.currentTarget)}
      onMouseLeave={(event) => { if (document.activeElement !== event.currentTarget) setTooltipPosition(null); }}
      type="button"
    >
      <img alt="" loading="lazy" src={event.snapshot_url} />
      {tooltipPosition ? createPortal(
        <div
          className={`iacs-tooltip report-snapshot-tooltip ${tooltipPosition.placement}`}
          id={tooltipId}
          ref={tooltipRef}
          role="tooltip"
          style={{ left: tooltipPosition.left, top: tooltipPosition.top, visibility: tooltipPosition.left === 0 ? "hidden" : "visible" }}
        >
          <img alt="" loading="lazy" src={event.snapshot_url} />
          <strong>{event.registration_number}</strong>
          <span>{event.snapshot_captured_at ? `Captured ${formatSiteDate(event.snapshot_captured_at, timezone)}` : `${event.type_label} at ${event.occurred_label ?? formatSiteDate(event.occurred_at, timezone)}`}</span>
        </div>,
        document.body
      ) : null}
    </button>
  );
}
