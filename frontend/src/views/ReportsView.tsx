import { AlertTriangle, BarChart3, Camera, CheckCircle2, Clock3, Download, HelpCircle, LogIn, LogOut, Search, UserRound } from "lucide-react";
import React from "react";
import { useDirectoryOptions } from "../features/directory/reads";
import { useReportActions } from "../features/reports/useReportActions";
import { useReportRange, useReportPreview } from "../features/reports/useReportPreview";
import { ReportPreview } from "../features/reports/ReportPreview";
import { ReportsDateTimePicker } from "../features/reports/ReportsDateTimePicker";
import { quickRanges, defaultOptions, siteCivilInput, reportInitials, vehicleLabel, personMetaLabel, visitorPassMetaLabel, visitorPassVehicleLabel, type QuickRange, type ReportOptions, type ReportSearchResult } from "../features/reports/model";
import { api } from "../api/client";
import { type ReportPreviewRequest } from "../api/reports";
import { matches } from "../lib/format";
import { mediaSource } from "../lib/media";

import type { AccessEvent, Person, Presence } from "../api/types";
import type { VisitorPass } from "../features/passes/types";

export function ReportsView({
  events, people, presence, refreshToken = 0
}: {
  events: AccessEvent[];
  people: Person[];
  presence: Presence[];
  refreshToken?: number;
}) {
  const personSearchLabelId = React.useId();
  const personSearchListId = React.useId();
  const [selectedPersonId, setSelectedPersonId] = React.useState("");
  const [selectedVisitorPassId, setSelectedVisitorPassId] = React.useState("");
  const [personQuery, setPersonQuery] = React.useState("");
  const [isPersonSearchOpen, setIsPersonSearchOpen] = React.useState(false);
  const directoryPeople = useDirectoryOptions("people", people, selectedPersonId ? [selectedPersonId] : [], true, refreshToken);
  React.useEffect(() => { directoryPeople.setQuery(isPersonSearchOpen ? personQuery : ""); }, [personQuery, isPersonSearchOpen]);
  const reportablePeople = React.useMemo(
    () => directoryPeople.items.filter((person) => person.is_active).sort((a, b) => a.display_name.localeCompare(b.display_name)),
    [directoryPeople.items]
  );
  React.useEffect(() => {
    const selected = directoryPeople.items.find((person) => person.id === selectedPersonId);
    if (selected?.is_active === false) { setSelectedPersonId(""); setPersonQuery(""); }
  }, [directoryPeople.items, selectedPersonId]);
  const [highlightedPersonIndex, setHighlightedPersonIndex] = React.useState(0);
  const { range, setRange, endInput, setEndInput, startInput, setStartInput, siteTimezone, setSiteTimezone,
    setRangeRevision, isLoadingContext, setIsLoadingContext, contextError, folds, setFolds } = useReportRange();
  const [options, setOptions] = React.useState<ReportOptions>(defaultOptions);
  const [visitorPasses, setVisitorPasses] = React.useState<VisitorPass[]>([]);
  const { loadedReport, setLoadedReport, isExportingReport, isLoadingReportId, reportActionError, setReportActionError,
    cancelReportAction, downloadReportPdf, loadSavedReport, exportReport } = useReportActions();
  const [helpOpen, setHelpOpen] = React.useState(false);
  const visitorPassesLoadedRef = React.useRef(false);
  React.useEffect(() => {
    if (selectedVisitorPassId && visitorPasses.some((visitorPass) => visitorPass.id === selectedVisitorPassId)) return;
    if (selectedVisitorPassId) {
      setSelectedVisitorPassId("");
      setPersonQuery("");
    }
  }, [selectedVisitorPassId, visitorPasses]);

  React.useEffect(() => {
    const shouldLoadVisitorPasses = isPersonSearchOpen || Boolean(personQuery.trim()) || Boolean(selectedVisitorPassId);
    if (!shouldLoadVisitorPasses || visitorPassesLoadedRef.current) return undefined;
    const controller = new AbortController();
    api.get<VisitorPass[]>("/api/v1/visitor-passes?limit=500", { signal: controller.signal })
      .then((nextVisitorPasses) => {
        if (controller.signal.aborted) return;
        visitorPassesLoadedRef.current = true;
        setVisitorPasses(nextVisitorPasses);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [isPersonSearchOpen, personQuery, selectedVisitorPassId]);

  const reportableVisitorPasses = React.useMemo(
    () => [...visitorPasses].sort((a, b) => a.visitor_name.localeCompare(b.visitor_name)),
    [visitorPasses]
  );
  const filteredPeople = React.useMemo(
    () => reportablePeople.filter((person) =>
      matches(person.display_name, personQuery) ||
      person.vehicles.some((vehicle) => matches(vehicle.registration_number, personQuery))
    ),
    [personQuery, reportablePeople]
  );
  const filteredVisitorPasses = React.useMemo(
    () => reportableVisitorPasses.filter((visitorPass) =>
      matches(visitorPass.visitor_name, personQuery) ||
      matches(visitorPass.number_plate || "", personQuery) ||
      matches(visitorPass.vehicle_make || "", personQuery) ||
      matches(visitorPass.vehicle_colour || "", personQuery)
    ),
    [personQuery, reportableVisitorPasses]
  );
  const reportIdCandidate = React.useMemo(() => {
    const trimmed = personQuery.trim();
    return /^\d{4,12}$/.test(trimmed) ? trimmed : "";
  }, [personQuery]);
  const personSearchResults = React.useMemo<ReportSearchResult[]>(() => {
    const peopleResults: ReportSearchResult[] = (personQuery.trim() ? filteredPeople : reportablePeople)
      .slice(0, 5)
      .map((person) => ({ type: "person", person }));
    const visitorPassResults: ReportSearchResult[] = (personQuery.trim() ? filteredVisitorPasses : reportableVisitorPasses)
      .slice(0, Math.max(0, (reportIdCandidate ? 7 : 8) - peopleResults.length))
      .map((visitorPass) => ({ type: "visitor_pass", visitorPass }));
    const subjectResults = [...peopleResults, ...visitorPassResults].slice(0, reportIdCandidate ? 7 : 8);
    return reportIdCandidate
      ? [{ type: "report", reportId: reportIdCandidate }, ...subjectResults]
      : subjectResults;
  }, [filteredPeople, filteredVisitorPasses, personQuery, reportIdCandidate, reportablePeople, reportableVisitorPasses]);

  React.useEffect(() => {
    setHighlightedPersonIndex(0);
  }, [personQuery, personSearchResults.length]);

  // Shell resources invalidate the backend read; they never supply partial report data.
  const previewRequest = React.useMemo<ReportPreviewRequest | null>(() => {
    if (loadedReport || isLoadingContext || contextError || !startInput || !endInput || (!selectedPersonId && !selectedVisitorPassId)) return null;
    return {
      person_id: selectedPersonId || undefined, visitor_pass_id: selectedVisitorPassId || undefined,
      period_start: startInput, period_end: endInput,
      include_denied: options.includeDenied, include_snapshots: options.includeSnapshots,
      include_confidence: options.includeConfidence, ...folds
    };
  }, [selectedPersonId, selectedVisitorPassId, startInput, endInput, options, folds, loadedReport,
    isLoadingContext, contextError, events, people, presence]);
  const { currentPreview, currentPreviewError, previewPending } = useReportPreview(previewRequest);
  const activeReport = loadedReport?.report ?? (currentPreview?.status === "ready" ? currentPreview.report : null);
  const previewPerson = activeReport?.person ?? null;
  const previewPersonPhoto = previewPerson ? mediaSource(previewPerson.profile_photo_url, previewPerson.profile_photo_data_url, "thumb") : "";
  const displayTimezone = activeReport?.period.timezone ?? siteTimezone;
  const previewSummary = {
    arrivals: activeReport?.summary.arrivals ?? "—", departures: activeReport?.summary.departures ?? "—",
    total: activeReport?.summary.total ?? "—", firstEvent: activeReport?.summary.first_event ?? "—"
  };
  const selectPerson = React.useCallback((person: Person) => {
    cancelReportAction();
    setSelectedPersonId(person.id);
    setSelectedVisitorPassId("");
    setPersonQuery(person.display_name);
    setIsPersonSearchOpen(false);
    setHighlightedPersonIndex(0);
    setLoadedReport(null);
    setReportActionError(null);
  }, [cancelReportAction]);

  const selectVisitorPass = React.useCallback((visitorPass: VisitorPass) => {
    setSelectedPersonId("");
    cancelReportAction();
    setSelectedVisitorPassId(visitorPass.id);
    setPersonQuery(visitorPass.visitor_name);
    setIsPersonSearchOpen(false);
    setHighlightedPersonIndex(0);
    setLoadedReport(null);
    setReportActionError(null);
  }, [cancelReportAction]);

  const loadReportById = React.useCallback(async (reportId: string) => {
    setRange("custom");
    const response = await loadSavedReport(reportId);
    if (!response) return;
    const matchingVisitorPass = response.report.subject_type === "visitor_pass"
      ? visitorPasses.find((visitorPass) => visitorPass.id === response.report.person.id) : null;
    setSiteTimezone(response.report.period.timezone); setFolds({});
    setSelectedPersonId(response.report.subject_type === "visitor_pass" ? "" : response.report.person.id);
    setSelectedVisitorPassId(matchingVisitorPass?.id ?? "");
    setPersonQuery(`Report #${response.report_id} - ${response.report.person.display_name}`);
    setStartInput(response.report.period.start); setEndInput(response.report.period.end);
    setOptions({ includeDenied: response.report.options.include_denied,
      includeSnapshots: response.report.options.include_snapshots, includeConfidence: response.report.options.include_confidence });
    setRange("custom"); setIsPersonSearchOpen(false); setHighlightedPersonIndex(0);
  }, [loadSavedReport, visitorPasses]);

  const exportCurrentReport = React.useCallback(async () => {
    if (currentPreview?.status !== "ready") return;
    const report = currentPreview.report;
    const response = await exportReport({ person_id: selectedPersonId || undefined, visitor_pass_id: selectedVisitorPassId || undefined,
      period_start: report.period.start, period_end: report.period.end, ...report.options });
    if (response) setPersonQuery(`Report #${response.report_id} - ${response.report.person.display_name}`);
  }, [currentPreview, exportReport, selectedPersonId, selectedVisitorPassId]);

  const handleReportExportClick = () => {
    if (loadedReport) {
      downloadReportPdf(loadedReport.download_url);
      return;
    }
    void exportCurrentReport();
  };

  const clearLoadedReport = () => {
    cancelReportAction();
    if (loadedReport) setLoadedReport(null);
    if (reportActionError) setReportActionError(null);
  };

  const handlePersonSearchKeyDown = (keyboardEvent: React.KeyboardEvent<HTMLInputElement>) => {
    if (keyboardEvent.key === "ArrowDown") {
      keyboardEvent.preventDefault();
      setIsPersonSearchOpen(true);
      setHighlightedPersonIndex((current) => Math.min(current + 1, Math.max(0, personSearchResults.length - 1)));
      return;
    }

    if (keyboardEvent.key === "ArrowUp") {
      keyboardEvent.preventDefault();
      setIsPersonSearchOpen(true);
      setHighlightedPersonIndex((current) => Math.max(0, current - 1));
      return;
    }

    if (keyboardEvent.key === "Enter") {
      const result = personSearchResults[Math.min(highlightedPersonIndex, personSearchResults.length - 1)];
      if (!result) return;
      keyboardEvent.preventDefault();
      if (result.type === "report") {
        void loadReportById(result.reportId);
      } else if (result.type === "visitor_pass") {
        selectVisitorPass(result.visitorPass);
      } else {
        selectPerson(result.person);
      }
      return;
    }

    if (keyboardEvent.key === "Escape") {
      setIsPersonSearchOpen(false);
    }
  };

  const applyQuickRange = (nextRange: QuickRange) => {
    clearLoadedReport();
    setIsLoadingContext(true);
    setRange(nextRange);
    setRangeRevision((revision) => revision + 1);
  };

  const updateOption = (key: keyof ReportOptions) => {
    clearLoadedReport();
    setOptions((current) => ({ ...current, [key]: !current[key] }));
  };

  return (
    <section className="reports-page">
      <div className="reports-page-head">
        <div>
          <h1>Reports</h1>
          <p>Access Arrivals / Departures</p>
        </div>
        <button className="report-help-button" type="button" aria-expanded={helpOpen} aria-controls="report-help" onClick={() => setHelpOpen((open) => !open)}>
          <HelpCircle size={16} /> How this report works
        </button>
      </div>
      {helpOpen ? <div className="card report-help" id="report-help"><h2>How this report works</h2><p>Choose a person or visitor pass, then a date range. Dates use the site timezone shown below the controls. At a repeated daylight saving time, choose the intended occurrence when prompted.</p><p>The preview reads the full selected period. Its event table may show a subset for space; the count states how many records the complete report contains. Export creates a saved PDF for the same subject, range and options. Wait for the preview to finish before exporting.</p></div> : null}

      <div className="report-builder-panel">
        <div className="report-builder-main">
          <div className="report-person-panel">
            <div
              className="report-field report-person-field"
              onBlur={(event) => {
                const nextTarget = event.relatedTarget;
                if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) return;
                setIsPersonSearchOpen(false);
              }}
            >
              <span id={personSearchLabelId}>Subject</span>
              <div className={`report-person-search ${previewPerson ? "" : "needs-selection"}`} role="presentation">
                <Search size={15} />
                <input
                  aria-activedescendant={isPersonSearchOpen && personSearchResults[highlightedPersonIndex] ? `${personSearchListId}-option-${highlightedPersonIndex}` : undefined}
                  aria-autocomplete="list"
                  aria-controls={personSearchListId}
                  aria-expanded={isPersonSearchOpen}
                  aria-labelledby={personSearchLabelId}
                  onChange={(event) => {
                    const nextValue = event.target.value;
                    clearLoadedReport();
                    setPersonQuery(nextValue);
                    setIsPersonSearchOpen(true);
                    setHighlightedPersonIndex(0);
                    if (!nextValue.trim()) {
                      setSelectedPersonId("");
                      setSelectedVisitorPassId("");
                    }
                  }}
                  onFocus={() => setIsPersonSearchOpen(true)}
                  onKeyDown={handlePersonSearchKeyDown}
                  placeholder="Search people, visitor passes or plates"
                  role="combobox"
                  type="text"
                  value={personQuery}
                />
              </div>
              {isPersonSearchOpen ? (
                <div className="report-person-results" id={personSearchListId} role="listbox">
                  {personSearchResults.length ? personSearchResults.map((result, index) => (
                    result.type === "report" ? (
                      <button
                        aria-selected={index === highlightedPersonIndex}
                        className={`report-result-report ${index === highlightedPersonIndex ? "active" : ""}`}
                        id={`${personSearchListId}-option-${index}`}
                        key={`report-${result.reportId}`}
                        onClick={() => void loadReportById(result.reportId)}
                        onMouseDown={(mouseEvent) => mouseEvent.preventDefault()}
                        onMouseEnter={() => setHighlightedPersonIndex(index)}
                        role="option"
                        type="button"
                      >
                        <span>
                          <strong>{isLoadingReportId ? "Opening Report" : `Open Report #${result.reportId}`}</strong>
                          <small>Load an exported PDF report snapshot</small>
                        </span>
                        <em>Report ID</em>
                      </button>
                    ) : result.type === "person" ? (
                      <button
                        aria-selected={result.person.id === selectedPersonId}
                        className={`${index === highlightedPersonIndex ? "active" : ""} ${result.person.id === selectedPersonId ? "selected" : ""}`}
                        id={`${personSearchListId}-option-${index}`}
                        key={result.person.id}
                        onClick={() => selectPerson(result.person)}
                        onMouseDown={(mouseEvent) => mouseEvent.preventDefault()}
                        onMouseEnter={() => setHighlightedPersonIndex(index)}
                        role="option"
                        type="button"
                      >
                        <span>
                          <strong>{result.person.display_name}</strong>
                          <small>{personMetaLabel(result.person)}</small>
                        </span>
                        <em>{vehicleLabel(result.person)}</em>
                      </button>
                    ) : (
                      <button
                        aria-selected={result.visitorPass.id === selectedVisitorPassId}
                        className={`${index === highlightedPersonIndex ? "active" : ""} ${result.visitorPass.id === selectedVisitorPassId ? "selected" : ""}`}
                        id={`${personSearchListId}-option-${index}`}
                        key={result.visitorPass.id}
                        onClick={() => selectVisitorPass(result.visitorPass)}
                        onMouseDown={(mouseEvent) => mouseEvent.preventDefault()}
                        onMouseEnter={() => setHighlightedPersonIndex(index)}
                        role="option"
                        type="button"
                      >
                        <span>
                          <strong>{result.visitorPass.visitor_name}</strong>
                          <small>{visitorPassMetaLabel(result.visitorPass)}</small>
                        </span>
                        <em>{visitorPassVehicleLabel(result.visitorPass)}</em>
                      </button>
                    )
                  )) : (
                    <div className="report-person-result-empty" role="presentation">
                      No people, visitor passes, plates, or report IDs match
                    </div>
                  )}
                </div>
              ) : null}
            </div>
            <div className="report-person-card">
              <span className="report-avatar small">
                {previewPersonPhoto
                  ? <img alt="" decoding="async" loading="lazy" src={previewPersonPhoto} />
                  : previewPerson ? reportInitials(previewPerson) : <UserRound size={18} />}
              </span>
              <div>
                {previewPerson ? (
                  <>
                    <strong>{previewPerson.display_name}</strong>
                    <span>{loadedReport ? `Exported Report #${loadedReport.report_id}` : personMetaLabel(previewPerson)}</span>
                    <span>{vehicleLabel(previewPerson)}</span>
                  </>
                ) : (
                  <>
                    <strong>No Subject Selected</strong>
                    <span>Choose a Person or Visitor Pass to generate report</span>
                  </>
                )}
              </div>
            </div>
          </div>

          <div className="report-period-panel">
            <div className="report-field">
              <span>Quick Period</span>
              <div className="report-quick-ranges" aria-label="Quick report periods">
                {quickRanges.map((item) => (
                  <button
                    className={range === item.value ? "active" : ""}
                    key={item.value}
                    onClick={() => applyQuickRange(item.value)}
                    type="button"
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="report-date-grid">
              <ReportsDateTimePicker
                label="From"
                onChange={(nextValue) => {
                  clearLoadedReport();
                  setRange("custom");
                  setStartInput(nextValue);
                  setFolds((current) => ({ ...current, period_start_fold: undefined }));
                }}
                value={siteCivilInput(startInput, displayTimezone)}
              />
              <ReportsDateTimePicker
                label="To"
                onChange={(nextValue) => {
                  clearLoadedReport();
                  setRange("custom");
                  setEndInput(nextValue);
                  setFolds((current) => ({ ...current, period_end_fold: undefined }));
                }}
                value={siteCivilInput(endInput, displayTimezone)}
              />
            </div>
            <div className="report-timezone-note">
              <Clock3 size={14} />
              Complete Preview | {displayTimezone || "Loading site timezone…"}
            </div>
          </div>

          <div className="report-include-panel">
            <span className="report-panel-label">Include</span>
            <button className={options.includeDenied ? "active" : ""} onClick={() => updateOption("includeDenied")} type="button">
              <span className="report-check-box"><AlertTriangle size={14} /></span> Denied attempts
            </button>
            <button className={options.includeSnapshots ? "active" : ""} onClick={() => updateOption("includeSnapshots")} type="button">
              <span className="report-check-box"><Camera size={14} /></span> Snapshots
            </button>
            <button className={options.includeConfidence ? "active" : ""} onClick={() => updateOption("includeConfidence")} type="button">
              <span className="report-check-box"><CheckCircle2 size={14} /></span> Confidence
            </button>
          </div>

          <div className="report-live-summary-panel">
            <span className="report-panel-label">{loadedReport ? "Report Summary" : "Preview Summary"}</span>
            <div><LogIn size={15} /><span>Arrivals</span><strong>{previewSummary.arrivals}</strong></div>
            <div><LogOut size={15} /><span>Departures</span><strong>{previewSummary.departures}</strong></div>
            <div><BarChart3 size={15} /><span>Total Events</span><strong>{previewSummary.total}</strong></div>
            <div><Clock3 size={15} /><span>First Event</span><strong>{previewSummary.firstEvent}</strong></div>
          </div>
        </div>

        <div className="report-builder-status">
          <div>
            <span className="report-live-dot" />
            <strong>{loadedReport ? `Report #${loadedReport.report_id}` : previewPending ? "Loading complete preview…" : activeReport ? "Complete preview" : "Choose a subject and period"}</strong>
          </div>
          <button
            className="report-export-button"
            disabled={(!loadedReport && currentPreview?.status !== "ready") || isExportingReport || isLoadingReportId}
            onClick={handleReportExportClick}
            type="button"
          >
            <Download size={16} /> {isExportingReport ? "Exporting PDF" : loadedReport ? "Download PDF" : "Export PDF"}
          </button>
        </div>
        {currentPreview?.status === "time_choice_required" ? currentPreview.time_choices.map((choice) => (
          <label className="report-field" key={choice.field}>
            <span>{choice.field === "period_start" ? "From" : "To"}: {choice.local_time.replace("T", " ")} occurs twice in {currentPreview.site_timezone}</span>
            <select aria-label={`${choice.field === "period_start" ? "From" : "To"} occurrence`}
              value={folds[`${choice.field}_fold`] ?? ""}
              onChange={(event) => setFolds((current) => ({ ...current, [`${choice.field}_fold`]: Number(event.target.value) as 0 | 1 }))}>
              <option value="" disabled>Choose an occurrence</option>
              {choice.choices.map((occurrence) => <option key={occurrence.fold} value={occurrence.fold}>{occurrence.label}</option>)}
            </select>
          </label>
        )) : null}
        {reportActionError || currentPreviewError || contextError || directoryPeople.error ? (
          <div className="report-action-error" role="alert">{reportActionError || currentPreviewError || contextError || directoryPeople.error}</div>
        ) : null}
      </div>

      {activeReport ? <ReportPreview activeReport={activeReport} reportId={loadedReport?.report_id} /> : null}
    </section>
  );
}
