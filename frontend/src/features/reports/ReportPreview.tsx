import { BarChart3, CalendarDays, Car, LogIn, LogOut, ShieldCheck, UserRound } from "lucide-react";
import type { ReportSnapshot, ReportSnapshotVehicle } from "../../api/reports";
import { titleCase } from "../../lib/format";
import { mediaSource } from "../../lib/media";
import { Badge } from "../../ui/primitives";
import { ReportDurationCell, ReportSnapshotThumb } from "./ReportEventCells";
import { eventIcon, formatSiteDate, personMetaLabel, reportInitials, vehicleLabel, reportVehicleTitle, reportComplianceTone, reportComplianceLabel } from "./model";

const dayRhythmTicks = [
  { left: 0, label: "12 AM" },
  { left: 100 / 6, label: "4 AM" },
  { left: 200 / 6, label: "8 AM" },
  { left: 50, label: "12 PM" },
  { left: 400 / 6, label: "4 PM" },
  { left: 500 / 6, label: "8 PM" },
  { left: 100, label: "12 AM" }
];

export function ReportPreview({ activeReport, reportId }: { activeReport: ReportSnapshot; reportId?: string }) {
  const loadedReport = reportId ? { report_id: reportId } : null;
  const previewPerson = activeReport.person;
  const previewPresence = activeReport?.presence ?? null;
  const previewSummary = {
    arrivals: activeReport?.summary.arrivals ?? "—", departures: activeReport?.summary.departures ?? "—",
    total: activeReport?.summary.total ?? "—", firstEvent: activeReport?.summary.first_event ?? "—"
  };
  const previewEvents = activeReport?.events ?? [];
  const previewEventCount = previewEvents.length;
  const previewVehicles: ReportSnapshotVehicle[] = previewPerson?.vehicles ?? [];
  const previewAllTimelineEvents = activeReport?.timeline.all ?? [];
  const previewSelectedTimelineEvents = activeReport?.timeline.selected ?? [];
  const previewPeriodLabel = activeReport?.period.label ?? "";
  const previewPeriodStartLabel = activeReport?.period.start_label ?? "";
  const previewPeriodEndLabel = activeReport?.period.end_label ?? "";
  const previewPeriodDurationLabel = activeReport?.period.duration_label ?? "";
  const previewGeneratedLabel = activeReport?.generated_label ?? "";
  const previewOptions = activeReport ? {
    includeDenied: activeReport.options.include_denied, includeSnapshots: activeReport.options.include_snapshots,
    includeConfidence: activeReport.options.include_confidence
  } : { includeDenied: false, includeSnapshots: true, includeConfidence: true };
  const previewSubjectKind = activeReport?.subject_type === "visitor_pass" ? "Visitor Pass" : "Person";
  const displayTimezone = activeReport?.period.timezone ?? "UTC";
  const previewPersonPhoto = previewPerson ? mediaSource(previewPerson.profile_photo_url, previewPerson.profile_photo_data_url, "thumb") : "";

  return (

      <div className="report-preview-shell">
        <div className="report-preview-header">
          <div>
            <h2>{loadedReport ? "Exported Report" : "Complete Preview"}</h2>
            <p>{previewPerson.display_name} {previewSubjectKind.toLowerCase()} movement report</p>
          </div>
          <Badge tone="blue">{loadedReport ? `Report #${loadedReport.report_id}` : "Complete history"}</Badge>
        </div>

        <article className="report-sheet" aria-label="Report preview">
          <header className="report-document-header">
            <div className="report-brand">
              <span className="report-brand-mark"><ShieldCheck size={20} /></span>
              <div>
                <strong>IACS</strong>
                <span>Intelligent Access Control System</span>
              </div>
            </div>
            <h2>{previewSubjectKind} Arrivals / Departures Report</h2>
            <span>Generated {previewGeneratedLabel}</span>
          </header>

          <section className="report-subject-band">
            <div className="report-subject-person">
              <span className="report-avatar">
                {previewPersonPhoto ? <img alt="" decoding="async" loading="lazy" src={previewPersonPhoto} /> : reportInitials(previewPerson)}
              </span>
              <div>
                <strong>{previewPerson.display_name}</strong>
                <span>{loadedReport ? `Report ID ${loadedReport.report_id}` : personMetaLabel(previewPerson)}</span>
                <span>{vehicleLabel(previewPerson)}</span>
              </div>
            </div>
            <div className="report-subject-period">
              <CalendarDays size={17} />
              <span>Period</span>
              <strong>{previewPeriodLabel}</strong>
            </div>
            <div className="report-subject-metrics">
              <span><LogIn size={16} /> Arrivals <strong>{previewSummary.arrivals}</strong></span>
              <span><LogOut size={16} /> Departures <strong>{previewSummary.departures}</strong></span>
              <span><BarChart3 size={16} /> Total <strong>{previewSummary.total}</strong></span>
            </div>
          </section>

          <div className="report-document-grid">
            <section className="report-table-panel">
              {previewEvents.length ? (
                <div className="report-table">
                  <div className="report-table-head">
                    <span>Date / Time</span>
                    <span>Type</span>
                    <span>Vehicle</span>
                    <span>Event Detail</span>
                    <span>Duration</span>
                    <span>Source</span>
                    <span>Photo</span>
                    <span>Conf.</span>
                  </div>
                  {previewEvents.map((event) => {
                    const Icon = eventIcon(event);
                    return (
                      <div className="report-table-row" key={event.id}>
                        <time className="report-cell-time">{event.occurred_label ?? formatSiteDate(event.occurred_at, displayTimezone)}</time>
                        <span className={`report-type-pill ${event.tone}`}><Icon size={15} /> {event.type_label}</span>
                        <strong className="report-cell-vehicle">{event.registration_number}</strong>
                        <span className="report-cell-detail">{event.detail ?? (event.decision === "granted" ? "Access granted" : "Access denied")}</span>
                        <span className="report-cell-duration">
                          <ReportDurationCell duration={event.duration ?? { label: "N/A", tone: "muted" }} />
                        </span>
                        <span className="report-cell-source">{event.source_label ?? event.source}</span>
                        {previewOptions.includeSnapshots ? <ReportSnapshotThumb event={event} timezone={displayTimezone} /> : <span className="report-table-excluded">Off</span>}
                        {previewOptions.includeConfidence ? <span className="report-confidence">{event.confidence_percent ?? Math.round(event.confidence * 100)}%</span> : <span className="report-table-excluded">Off</span>}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="report-empty-state">
                  <UserRound size={28} />
                  <strong>No movements in this period</strong>
                  <span>Try a longer quick range or choose a subject with access events.</span>
                </div>
              )}
              {previewEventCount ? (
                <div className="report-table-footer">
                  Showing {previewEvents.length} of {previewEventCount} events
                </div>
              ) : null}
            </section>

            <aside className="report-side-panel">
              <div>
                <h3>Period Summary</h3>
                {loadedReport ? <p><span>Report ID</span><strong>{loadedReport.report_id}</strong></p> : null}
                <p><span>Start</span><strong>{previewPeriodStartLabel}</strong></p>
                <p><span>End</span><strong>{previewPeriodEndLabel}</strong></p>
                <p><span>Duration</span><strong>{previewPeriodDurationLabel}</strong></p>
                <p><span>Presence</span><strong>{previewPresence ? titleCase(previewPresence.state) : "Unknown"}</strong></p>
              </div>
              <div>
                <h3>Vehicle Summary</h3>
                {previewVehicles.map((vehicle) => (
                  <div className="report-vehicle-summary-card" key={vehicle.id}>
                    <div className="report-vehicle-summary-title">
                      <Car size={15} />
                      <div>
                        <strong>{reportVehicleTitle(vehicle)}</strong>
                        <span>{vehicle.registration_number}</span>
                      </div>
                    </div>
                    <div className="report-vehicle-compliance-row">
                      <span>MOT</span>
                      <strong className={`report-compliance-pill ${vehicle.mot_tone ?? reportComplianceTone(vehicle.mot_status)}`}>
                        {vehicle.mot_label ?? reportComplianceLabel(vehicle.mot_expiry)}
                      </strong>
                    </div>
                    <div className="report-vehicle-compliance-row">
                      <span>Tax</span>
                      <strong className={`report-compliance-pill ${vehicle.tax_tone ?? reportComplianceTone(vehicle.tax_status)}`}>
                        {vehicle.tax_label ?? reportComplianceLabel(vehicle.tax_expiry)}
                      </strong>
                    </div>
                  </div>
                ))}
                {!previewVehicles.length ? <p><span>No registered vehicles</span></p> : null}
              </div>
            </aside>
          </div>

          <section className="report-period-timeline-section">
            <div className="report-section-heading">
              <h3>Timeline (All-Day Rhythm)</h3>
            </div>
            {previewAllTimelineEvents.length ? (
              <div className="report-period-timeline">
                <div className="report-period-labels">
                  {dayRhythmTicks.map((tick) => (
                    <span key={`${tick.left}-${tick.label}`} style={{ left: `${tick.left}%` }}>{tick.label}</span>
                  ))}
                </div>
                <div className="report-period-track">
                  {dayRhythmTicks.map((tick) => (
                    <span className="report-period-tick" key={`${tick.left}-${tick.label}`} style={{ left: `${tick.left}%` }} />
                  ))}
                  {previewAllTimelineEvents.map((event) => (
                    <span
                      className="report-period-marker grey"
                      key={`all-${event.id}`}
                      style={{ left: `${event.progress}%` }}
                      title={`All vehicles · ${event.label} · ${formatSiteDate(event.occurred_at, displayTimezone)} · ${event.registration_number}`}
                    />
                  ))}
                  {previewSelectedTimelineEvents.map((event) => (
                    <span
                      className={`report-period-marker ${event.tone}`}
                      key={event.id}
                      style={{ left: `${event.progress}%` }}
                      title={`${event.label} · ${formatSiteDate(event.occurred_at, displayTimezone)} · ${event.registration_number}`}
                    />
                  ))}
                </div>
                <div className="report-period-legend">
                  <span><i className="grey" /> All vehicles</span>
                  <span><i className="green" /> {previewSubjectKind} arrivals</span>
                  <span><i className="blue" /> {previewSubjectKind} departures</span>
                </div>
              </div>
            ) : (
              <p className="report-muted-copy">No vehicle arrivals or departures are available in the selected window.</p>
            )}
          </section>
        </article>
      </div>
  );
}
