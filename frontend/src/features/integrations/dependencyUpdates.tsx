import { Bot, CheckCircle2, Loader2, Play, RefreshCcw, RefreshCw, Save, ShieldCheck, X } from "lucide-react";
import React from "react";
import { wsUrl } from "../../api/client";
import { formatDate, formatFileSize, stringPayload, titleCase } from "../../lib/format";
import { Badge, EmptyState } from "../../ui/primitives";
import type { BadgeTone } from "../../ui/primitives";
import {
  DependencyAnalysis,
  DependencyBackup,
  DependencyCheckAllResult,
  DependencyConfirmAction,
  DependencyFailureDiagnosis,
  DependencyJob,
  DependencyJobEvent,
  DependencyPackage,
  DependencyStorageStatus,
  integrationsApi,
  compactDependencyJobEvent,
  DEPENDENCY_JOB_EVENT_LIMIT
} from "../../api/integrations";
import { ProtectAnalysisLine, renderInlineMarkdown } from "./unifiProtect";
function dependencyUpdateTone(dependency: DependencyPackage): BadgeTone {
  if (dependency.update_available) return "amber";
  if (dependency.last_checked_at) return "green";
  return "gray";
}
function dependencyUpdateLabel(dependency: DependencyPackage): string {
  if (dependency.update_available && !dependencyCanApply(dependency)) return "Transitive Update";
  if (dependency.update_available) return "Update Available";
  if (dependency.last_checked_at) return "Current";
  return "Unchecked";
}
function dependencyCanApply(dependency: DependencyPackage): boolean {
  return dependency.ecosystem === "docker_image" || dependency.is_direct;
}
export function dependencyIsActionableUpdate(dependency: DependencyPackage): boolean {
  return dependency.update_available && dependencyCanApply(dependency);
}
function dependencyJobProgress(job: DependencyJob | null, events: DependencyJobEvent[]) {
  const phase = job?.phase || [...events].reverse().find((event) => event.phase)?.phase || "starting";
  const status = job?.status || "queued";
  const phaseProgress: Record<string, number> = {
    queued: 3,
    starting: 8,
    backup: 22,
    validate_backup: 22,
    apply: 55,
    restore_files: 55,
    verify: 82,
    rollback: 92,
    completed: 100,
    failed: 100
  };
  const labelMap: Record<string, string> = {
    queued: "Queued",
    starting: "Starting",
    backup: "Creating offline backup",
    validate_backup: "Validating backup",
    apply: "Applying update",
    restore_files: "Restoring files",
    verify: "Verifying",
    rollback: "Rolling back",
    completed: "Completed",
    failed: "Failed"
  };
  return {
    percent: status === "completed" ? 100 : status === "failed" ? 100 : phaseProgress[phase] ?? 10,
    label: labelMap[phase] || titleCase(phase),
    phase,
    status
  };
}
function formatDependencyLogTime(value?: string) {
  if (!value) return "now";
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function dependencyLogTypeLabel(value: string) {
  if (value === "stdout") return "log";
  if (value === "connection.ready") return "ready";
  return value.replaceAll("_", " ");
}
function dependencyJobDiagnosis(job: DependencyJob | null): DependencyFailureDiagnosis | null {
  const value = job?.result?.diagnosis;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const payload = value as Record<string, unknown>;
  return {
    category: stringPayload(payload.category) || "unknown",
    title: stringPayload(payload.title) || "Update job failed",
    summary: stringPayload(payload.summary) || "The update did not complete.",
    safe_state: stringPayload(payload.safe_state) || "IACS stopped before promoting unverified runtime changes.",
    retry_recommendation: stringPayload(payload.retry_recommendation) || "Review the logs and retry when the blocker is resolved.",
    actions: arrayOfStrings(payload.actions),
    affected_packages: arrayOfStrings(payload.affected_packages),
    command: stringPayload(payload.command),
    technical_detail: stringPayload(payload.technical_detail)
  };
}
function dependencyRollbackSummary(job: DependencyJob | null): string {
  const rollback = job?.result?.rollback;
  if (!rollback || typeof rollback !== "object" || Array.isArray(rollback)) {
    return "No live manifests were promoted.";
  }
  const payload = rollback as Record<string, unknown>;
  if (payload.restored === true) return "Offline backup restored; live manifests are back to their pre-update state.";
  if (payload.attempted === true) return "Rollback was attempted. Review the job logs to confirm the restore result.";
  return "No live manifests were promoted.";
}
function arrayOfStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}
function verificationStepDetails(step: string) {
  const clean = step.trim();
  const explicit = clean.match(/^\[(automated|operator|manual|iacs)\]\s*(.+)$/i);
  if (explicit) {
    const type = explicit[1].toLowerCase();
    return {
      label: type === "operator" || type === "manual" ? "Operator" : "IACS job",
      text: explicit[2],
      tone: type === "operator" || type === "manual" ? "amber" as BadgeTone : "blue" as BadgeTone
    };
  }
  const automated = /\b(npm run build|frontend build|compile|pytest|unit test|health|typecheck|lint)\b/i.test(clean);
  return {
    label: automated ? "IACS job" : "Operator",
    text: clean,
    tone: automated ? "blue" as BadgeTone : "amber" as BadgeTone
  };
}
function parseSuggestedDiff(diff: string) {
  const files: Array<{ file: string; added: number; removed: number; lines: string[] }> = [];
  let current: { file: string; added: number; removed: number; lines: string[] } | null = null;
  for (const line of diff.split(/\r?\n/)) {
    const fileMatch = line.match(/^\*\*\* (?:Update|Add|Delete) File:\s+(.+)$/);
    if (fileMatch) {
      current = { file: fileMatch[1], added: 0, removed: 0, lines: [line] };
      files.push(current);
      continue;
    }
    if (!current) {
      current = { file: "Suggested patch", added: 0, removed: 0, lines: [] };
      files.push(current);
    }
    current.lines.push(line);
    if (line.startsWith("+") && !line.startsWith("+++")) current.added += 1;
    if (line.startsWith("-") && !line.startsWith("---")) current.removed += 1;
  }
  return files;
}
export function DependencyUpdatesHub({
  packages,
  storage,
  loading,
  error,
  onChanged,
  onInspect
}: {
  packages: DependencyPackage[];
  storage: DependencyStorageStatus | null;
  loading: boolean;
  error: string;
  onChanged: () => Promise<void>;
  onInspect: (dependency: DependencyPackage) => void;
}) {
  const [checkingAll, setCheckingAll] = React.useState(false);
  const [checkSummary, setCheckSummary] = React.useState<DependencyCheckAllResult | null>(null);
  const [checkError, setCheckError] = React.useState("");
  const [showAll, setShowAll] = React.useState(false);
  const updateRows = React.useMemo(() => packages.filter(dependencyIsActionableUpdate), [packages]);
  const transitiveUpdateCount = packages.filter((dependency) => dependency.update_available && !dependencyCanApply(dependency)).length;
  const rows = showAll ? packages : updateRows;
  const checkedCount = packages.filter((dependency) => dependency.last_checked_at).length;
  const directCount = packages.filter((dependency) => dependency.is_direct).length;
  const sync = async () => {
    await integrationsApi.syncDependencies();
    await onChanged();
  };
  const checkAll = async () => {
    setCheckingAll(true);
    setCheckSummary(null);
    setCheckError("");
    try {
      const result = await integrationsApi.checkDependencies();
      setCheckSummary(result);
      await onChanged();
      setShowAll(false);
    } catch (nextError) {
      setCheckError(nextError instanceof Error ? nextError.message : "Unable to check dependencies.");
    } finally {
      setCheckingAll(false);
    }
  };
  return (
    <div className="dependency-updates-page">
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {checkError ? <div className="auth-error inline-error">{checkError}</div> : null}
      <DependencyStoragePanel storage={storage} onChanged={onChanged} />
      <section className="card dependency-update-table-card">
        <div className="dependency-update-table-head">
          <div>
            <h2>{showAll ? "Enrolled Dependencies" : "Available Updates"}</h2>
            <p>
              {showAll
                ? "All auto-enrolled external packages, including dependencies that are current or not checked yet."
                : updateRows.length
                  ? "Direct packages and images with newer versions that can be applied from IACS."
                  : checkedCount
                    ? "No actionable updates are currently known."
                    : "Run Check All to compare enrolled packages with their registries."}
            </p>
          </div>
          <div className="dependency-update-actions">
            <button className="primary-button" onClick={checkAll} disabled={loading || checkingAll} type="button">
              {checkingAll ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />} Check All
            </button>
            <button className="secondary-button" onClick={sync} disabled={loading} type="button">
              <RefreshCcw size={15} /> Sync Enrollment
            </button>
            <button className="secondary-button" onClick={onChanged} disabled={loading} type="button">
              <RefreshCw size={15} /> Refresh
            </button>
            <button className={showAll ? "secondary-button active" : "secondary-button"} onClick={() => setShowAll((value) => !value)} disabled={loading} type="button">
              {showAll ? "Show Updates" : "Show All"}
            </button>
          </div>
        </div>
        <div className="dependency-update-metrics">
          <div><span>Actionable</span><strong>{updateRows.length}</strong></div>
          <div><span>Enrolled</span><strong>{packages.length}</strong></div>
          <div><span>Direct</span><strong>{directCount}</strong></div>
          <div><span>Checked</span><strong>{checkedCount}</strong></div>
          <div><span>Transitive</span><strong>{transitiveUpdateCount}</strong></div>
        </div>
        {checkSummary ? (
          <div className={checkSummary.failed ? "dependency-check-summary warning" : "dependency-check-summary"}>
            Checked {checkSummary.checked} packages and found {checkSummary.updates} registry updates.
            {updateRows.length ? ` ${updateRows.length} can be applied directly from this hub.` : ""}
            {transitiveUpdateCount ? ` ${transitiveUpdateCount} transitive lockfile updates are available under Show All.` : ""}
            {checkSummary.failed ? ` ${checkSummary.failed} checks failed; see Updates & Rollbacks logs for details.` : ""}
          </div>
        ) : null}
        {loading || checkingAll ? <div className="loading-panel">{checkingAll ? "Checking every enrolled dependency" : "Loading dependency updates"}</div> : null}
        {!loading && !checkingAll && rows.length ? (
          <div className="dependency-update-table">
            <div className="dependency-update-row header">
              <span>Package</span>
              <span>Dependant</span>
              <span>Current</span>
              <span>New</span>
              <span>{showAll ? "Update" : "Risk"}</span>
              <span />
            </div>
            {rows.map((dependency) => (
              <div className="dependency-update-row" key={dependency.id}>
                <div>
                  <strong>{dependency.package_name}</strong>
                  <small>{dependency.ecosystem} · {dependency.is_direct ? "direct" : "transitive"}</small>
                </div>
                <span>{dependency.dependant_area}</span>
                <code>{dependency.current_version || "unknown"}</code>
                <code>{dependency.latest_version || "unchecked"}</code>
                <Badge tone={showAll ? dependencyUpdateTone(dependency) : riskTone(dependency.risk_status)}>
                  {showAll ? dependencyUpdateLabel(dependency) : titleCase(String(dependency.risk_status || "unknown"))}
                </Badge>
                <button className="secondary-button" onClick={() => onInspect(dependency)} type="button">
                  {dependency.update_available && dependencyCanApply(dependency) ? "Inspect/Update" : "Inspect"}
                </button>
              </div>
            ))}
          </div>
        ) : null}
        {!loading && !checkingAll && !rows.length ? (
          <div className="dependency-empty-state">
            <EmptyState icon={RefreshCcw} label={packages.length ? "No actionable updates." : "No dependencies enrolled yet."} />
            {packages.length ? <p>{checkedCount ? transitiveUpdateCount ? `${transitiveUpdateCount} transitive update${transitiveUpdateCount === 1 ? "" : "s"} can be inspected in Show All, but should move through their direct parent dependency.` : "Everything currently checked is up to date." : "Run Check All to populate this hub."}</p> : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}
export function DependencyUpdatePanel({
  packages,
  storage,
  onChanged
}: {
  packages: DependencyPackage[];
  storage: DependencyStorageStatus | null;
  onChanged: () => Promise<void>;
}) {
  const [selected, setSelected] = React.useState<DependencyPackage | null>(packages.find(dependencyIsActionableUpdate) ?? packages.find((dependency) => dependency.update_available) ?? packages[0] ?? null);
  React.useEffect(() => {
    setSelected((current) => current && packages.some((dependency) => dependency.id === current.id)
      ? packages.find((dependency) => dependency.id === current.id) ?? current
      : packages.find(dependencyIsActionableUpdate) ?? packages.find((dependency) => dependency.update_available) ?? packages[0] ?? null);
  }, [packages]);
  if (!packages.length) return <div className="empty-state">No enrolled dependencies are linked to this integration yet</div>;
  return (
    <div className="dependency-integration-panel">
      <div className="dependency-package-list">
        {packages.map((dependency) => (
          <button className={selected?.id === dependency.id ? "dependency-package-button active" : "dependency-package-button"} key={dependency.id} onClick={() => setSelected(dependency)} type="button">
            <span>
              <strong>{dependency.package_name}</strong>
              <small>{dependency.current_version || "unknown"}{" -> "}{dependency.latest_version || "unchecked"}</small>
            </span>
            <Badge tone={dependencyUpdateTone(dependency)}>{dependencyUpdateLabel(dependency)}</Badge>
          </button>
        ))}
      </div>
      {selected ? (
        <DependencyUpdateDeepDive dependency={selected} embedded storage={storage} onChanged={onChanged} />
      ) : null}
    </div>
  );
}
export function DependencyUpdateModal({
  dependency,
  storage,
  onClose,
  onChanged
}: {
  dependency: DependencyPackage;
  storage: DependencyStorageStatus | null;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card dependency-update-modal" role="dialog" aria-modal="true" aria-labelledby="dependency-update-title">
        <div className="modal-header">
          <div>
            <h2 id="dependency-update-title">{dependency.package_name}</h2>
            <p>{dependency.dependant_area} · {dependency.ecosystem}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close"><X size={16} /></button>
        </div>
        <DependencyUpdateDeepDive dependency={dependency} storage={storage} onChanged={onChanged} />
      </div>
    </div>
  );
}
function DependencyUpdateDeepDive({
  dependency,
  storage,
  embedded = false,
  onChanged
}: {
  dependency: DependencyPackage;
  storage: DependencyStorageStatus | null;
  embedded?: boolean;
  onChanged: () => Promise<void>;
}) {
  const [current, setCurrent] = React.useState(dependency);
  const [analysis, setAnalysis] = React.useState<DependencyAnalysis | null>(dependency.latest_analysis);
  const [backups, setBackups] = React.useState<DependencyBackup[]>([]);
  const [job, setJob] = React.useState<DependencyJob | null>(null);
  const [jobEvents, setJobEvents] = React.useState<DependencyJobEvent[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [confirmAction, setConfirmAction] = React.useState<DependencyConfirmAction | null>(null);
  const jobSocketRef = React.useRef<WebSocket | null>(null);
  const loadBackups = React.useCallback(async () => {
    setBackups(await integrationsApi.getDependencyBackups(dependency.id));
  }, [dependency.id]);
  const loadCurrentDependency = React.useCallback(async () => {
    const packages = await integrationsApi.getDependencyPackages();
    const next = packages.find((candidate) => candidate.id === dependency.id);
    if (!next) return null;
    setCurrent(next);
    setAnalysis(next.latest_analysis);
    return next;
  }, [dependency.id]);
  React.useEffect(() => {
    setCurrent(dependency);
    setAnalysis(dependency.latest_analysis);
    loadBackups().catch(() => undefined);
  }, [dependency, loadBackups]);
  const check = async () => {
    setLoading(true);
    setError("");
    try {
      const next = await integrationsApi.checkDependency(dependency.id);
      setCurrent(next);
      setAnalysis(next.latest_analysis);
      await onChanged();
    } catch (checkError) {
      setError(checkError instanceof Error ? checkError.message : "Unable to check for updates.");
    } finally {
      setLoading(false);
    }
  };
  const analyze = async () => {
    setLoading(true);
    setError("");
    try {
      const next = await integrationsApi.analyzeDependency(dependency.id, current.latest_version);
      setAnalysis(next);
      await onChanged();
    } catch (analysisError) {
      setError(analysisError instanceof Error ? analysisError.message : "Unable to analyze this update.");
    } finally {
      setLoading(false);
    }
  };
  const closeJobSocket = React.useCallback((socket?: WebSocket) => {
    const target = socket ?? jobSocketRef.current;
    if (!target) return;
    target.onmessage = null;
    target.onerror = null;
    target.onclose = null;
    if (jobSocketRef.current === target) jobSocketRef.current = null;
    if (target.readyState === WebSocket.CONNECTING || target.readyState === WebSocket.OPEN) {
      target.close();
    }
  }, []);
  React.useEffect(() => () => closeJobSocket(), [closeJobSocket]);
  const appendJobEvent = React.useCallback((event: DependencyJobEvent) => {
    setJobEvents((events) => [...events, compactDependencyJobEvent(event)].slice(-DEPENDENCY_JOB_EVENT_LIMIT));
  }, []);
  const refreshJobAfterStreamIssue = React.useCallback(async (jobId: string, context: string) => {
    try {
      const [nextJob] = await Promise.all([
        integrationsApi.getDependencyJob(jobId),
        onChanged(),
        loadCurrentDependency(),
        loadBackups()
      ]);
      setJob(nextJob);
    } catch (refreshError) {
      const message = refreshError instanceof Error ? refreshError.message : "Unable to refresh dependency job state.";
      setError(message);
      appendJobEvent({
        type: "refresh_failed",
        job_id: jobId,
        created_at: new Date().toISOString(),
        phase: "refresh",
        message: `${context}: ${message}`
      });
    }
  }, [appendJobEvent, loadBackups, loadCurrentDependency, onChanged]);
  const openJobSocket = React.useCallback((jobId: string) => {
    closeJobSocket();
    const socket = new WebSocket(wsUrl(`/api/v1/dependency-updates/jobs/${jobId}/ws`));
    jobSocketRef.current = socket;
    socket.onmessage = (event) => {
      let parsed: DependencyJobEvent;
      try {
        parsed = JSON.parse(event.data) as DependencyJobEvent;
      } catch (parseError) {
        console.warn("Ignored malformed dependency job stream event", {
          error: parseError instanceof Error ? parseError.message : String(parseError),
          bytes: typeof event.data === "string" ? event.data.length : undefined
        });
        appendJobEvent({
          type: "stream_error",
          job_id: jobId,
          created_at: new Date().toISOString(),
          phase: "stream",
          message: "Dependency update stream sent malformed JSON; refreshing job state."
        });
        void refreshJobAfterStreamIssue(jobId, "Malformed dependency job stream");
        closeJobSocket(socket);
        return;
      }
      const next = compactDependencyJobEvent(parsed);
      if (next.type === "connection.ready") return;
      appendJobEvent(next);
      if (next.phase) {
        setJob((currentJob) => currentJob ? { ...currentJob, phase: next.phase || currentJob.phase } : currentJob);
      }
      if (next.type === "completed" || next.type === "failed") {
        setJob((currentJob) => currentJob ? {
          ...currentJob,
          status: next.type === "completed" ? "completed" : "failed",
          phase: next.phase || currentJob.phase,
          error: next.type === "failed" ? next.message || currentJob.error : currentJob.error,
          result: next.result || currentJob.result
        } : currentJob);
        if (next.type === "completed") {
          setCurrent((dependencyState) => ({
            ...dependencyState,
            current_version: dependencyState.latest_version || dependencyState.current_version,
            update_available: false,
            risk_status: "safe"
          }));
        }
        closeJobSocket(socket);
        void refreshJobAfterStreamIssue(jobId, "Dependency job finished");
      }
    };
    socket.onerror = () => {
      console.warn("Dependency job websocket error; refreshing job state", { jobId });
      appendJobEvent({
        type: "stream_error",
        job_id: jobId,
        created_at: new Date().toISOString(),
        phase: "stream",
        message: "Dependency update live stream failed; refreshing job state."
      });
      void refreshJobAfterStreamIssue(jobId, "Dependency job stream failed");
      closeJobSocket(socket);
    };
    socket.onclose = () => {
      if (jobSocketRef.current === socket) jobSocketRef.current = null;
    };
  }, [appendJobEvent, closeJobSocket, refreshJobAfterStreamIssue]);
  const startApplyUpdate = async () => {
    setLoading(true);
    setError("");
    setJobEvents([]);
    try {
      const nextJob = await integrationsApi.applyDependency(dependency.id, current.latest_version);
      setJob(nextJob);
      openJobSocket(nextJob.id);
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : "Unable to start update job.");
    } finally {
      setLoading(false);
    }
  };
  const startRestoreBackup = async (backup: DependencyBackup) => {
    setLoading(true);
    setError("");
    setJobEvents([]);
    try {
      const nextJob = await integrationsApi.restoreDependencyBackup(backup.id);
      setJob(nextJob);
      openJobSocket(nextJob.id);
    } catch (restoreError) {
      setError(restoreError instanceof Error ? restoreError.message : "Unable to start restore job.");
    } finally {
      setLoading(false);
    }
  };
  const confirmSelectedAction = async () => {
    const action = confirmAction;
    if (!action) return;
    setConfirmAction(null);
    if (action.kind === "apply") {
      await startApplyUpdate();
    } else {
      await startRestoreBackup(action.backup);
    }
  };
  const updateActionAvailable = Boolean(current.update_available && current.latest_version);
  const checked = Boolean(current.last_checked_at);
  const analysisMatchesTarget = Boolean(analysis && current.latest_version && analysis.target_version === current.latest_version);
  const analysisRequired = updateActionAvailable && !analysisMatchesTarget;
  const breakingBlocked = updateActionAvailable && analysisMatchesTarget && String(analysis?.verdict || "").toLowerCase() === "breaking";
  const applyActionAvailable = updateActionAvailable && dependencyCanApply(current) && !analysisRequired && !breakingBlocked;
  const applyActionTitle = applyActionAvailable
    ? "Apply this update"
    : breakingBlocked
      ? "Breaking updates are blocked until the migration is resolved and analysis is re-run"
      : analysisRequired
        ? "Analyze this target version before applying"
        : updateActionAvailable
          ? "Transitive packages must be updated through their direct dependency"
          : checked
            ? "No update is available to apply"
            : "Check this dependency first";
  const jobActive = job?.status === "queued" || job?.status === "running";
  const jobCompleted = job?.status === "completed";
  const hasExecution = Boolean(job || jobEvents.length);
  return (
    <div className={embedded ? "dependency-update-deep-dive embedded" : "dependency-update-deep-dive"}>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      <div className="dependency-version-strip">
        <div><span>Current</span><strong>{current.current_version || "unknown"}</strong></div>
        <div><span>Latest</span><strong>{current.latest_version || "unchecked"}</strong></div>
        <div><span>Storage</span><strong>{storage?.config_status || "unknown"}</strong></div>
        <Badge tone={dependencyUpdateTone(current)}>{dependencyUpdateLabel(current)}</Badge>
      </div>
      <div className="dependency-update-actions">
        <button className="secondary-button" onClick={check} disabled={loading} type="button">
          <RefreshCcw size={15} /> Check
        </button>
        {jobCompleted ? (
          <button className="secondary-button" disabled type="button">
            <CheckCircle2 size={15} /> Update Complete
          </button>
        ) : (
          <>
            <button className="primary-button" onClick={analyze} disabled={loading || jobActive || !updateActionAvailable} title={updateActionAvailable ? "Analyze this update" : checked ? "No update is available to analyze" : "Check this dependency first"} type="button">
              <Bot size={15} /> Analyze
            </button>
            <button
              className="primary-button"
              onClick={() => setConfirmAction({ kind: "apply" })}
              disabled={loading || jobActive || !applyActionAvailable}
              title={applyActionTitle}
              type="button"
            >
              <Play size={15} /> Proceed with Update
            </button>
          </>
        )}
      </div>
      {updateActionAvailable && !dependencyCanApply(current) ? (
        <div className="dependency-check-summary warning">This package is transitive. Review the analysis here, then update the owning direct dependency or lockfile.</div>
      ) : null}
      {updateActionAvailable && dependencyCanApply(current) && analysisRequired ? (
        <div className="dependency-check-summary warning">Run analysis for {current.latest_version} before applying so IACS can review changelog risk against local usage.</div>
      ) : null}
      {breakingBlocked ? (
        <div className="dependency-check-summary danger">IACS blocked this update because the latest analysis marked it Breaking. Resolve the proposed migration, run the build checks, then re-run analysis before applying.</div>
      ) : null}
      {hasExecution ? (
        <DependencyLiveExecution
          events={jobEvents}
          job={job}
          onRetry={() => setConfirmAction({ kind: "apply" })}
          retryDisabled={loading || !applyActionAvailable || jobActive}
        />
      ) : (
        <>
          <section className="dependency-analysis-panel">
            <div className="dependency-panel-title">
              <strong>LLM Analysis</strong>
              {analysis ? <Badge tone={riskTone(analysis.verdict)}>{titleCase(String(analysis.verdict))}</Badge> : <Badge tone="gray">Not Analyzed</Badge>}
            </div>
            {analysis ? (
              <DependencyAnalysisReview analysis={analysis} />
            ) : (
              <div className="empty-state">Run analysis to review changelog risk and local code usage.</div>
            )}
          </section>
          {analysis?.suggested_diff ? <DependencySuggestedFixes diff={analysis.suggested_diff} /> : null}
        </>
      )}
      <section className="dependency-backup-panel">
        <div className="dependency-panel-title">
          <strong>Backup History</strong>
          <Badge tone="gray">{backups.length}</Badge>
        </div>
        {backups.length ? backups.map((backup) => (
          <div className="dependency-backup-row" key={backup.id}>
            <div>
              <strong>{backup.version || "unknown"} · {backup.reason}</strong>
              <span>{formatDate(backup.created_at)} · {formatFileSize(backup.size_bytes)}</span>
            </div>
            <button className="secondary-button" onClick={() => setConfirmAction({ kind: "restore", backup })} disabled={loading || jobActive} type="button">
              <ShieldCheck size={15} /> Restore
            </button>
          </div>
        )) : <div className="empty-state">No backups have been created for this package.</div>}
      </section>
      {confirmAction ? (
        <DependencyUpdateConfirmModal
          action={confirmAction}
          dependency={current}
          loading={loading}
          onCancel={() => setConfirmAction(null)}
          onConfirm={confirmSelectedAction}
        />
      ) : null}
    </div>
  );
}
function DependencyAnalysisReview({ analysis }: { analysis: DependencyAnalysis }) {
  const summaryLines = analysis.summary_markdown.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return (
    <div className="dependency-analysis-review">
      <section className="dependency-analysis-card">
        <h4>Summary</h4>
        <div className="dependency-analysis-lines">
          {summaryLines.map((line, index) => (
            <ProtectAnalysisLine line={line} key={`${analysis.id}-summary-${index}`} />
          ))}
        </div>
      </section>
      <section className="dependency-analysis-card">
        <div className="dependency-verification-title">
          <h4>Verification Plan</h4>
          <Badge tone="blue">Guided</Badge>
        </div>
        <p>IACS runs install, build, and health checks during Live Execution. These LLM-generated steps are the remaining checks to confirm the affected feature still behaves correctly.</p>
        <div className="dependency-verification-list">
          {analysis.verification_steps.length ? analysis.verification_steps.map((step, index) => {
            const details = verificationStepDetails(step);
            return (
              <div className="dependency-verification-step" key={`${analysis.id}-verify-${index}`}>
                <Badge tone={details.tone}>{details.label}</Badge>
                <span>{renderInlineMarkdown(details.text)}</span>
              </div>
            );
          }) : (
            <span className="dependency-muted">No extra verification steps were suggested.</span>
          )}
        </div>
      </section>
    </div>
  );
}
function DependencySuggestedFixes({ diff }: { diff: string }) {
  const files = React.useMemo(() => parseSuggestedDiff(diff), [diff]);
  return (
    <section className="dependency-fix-panel">
      <div className="dependency-panel-title">
        <div>
          <strong>Proposed Fixes</strong>
          <p>LLM-generated patch guidance. IACS applies package-manager changes automatically when you proceed.</p>
        </div>
        <Badge tone="gray">Draft</Badge>
      </div>
      <div className="dependency-fix-files">
        {files.map((file) => (
          <details className="dependency-fix-file" key={file.file}>
            <summary>
              <span>
                <strong>{file.file}</strong>
                <small>{file.added} added · {file.removed} removed</small>
              </span>
              <span className="dependency-fix-toggle">Show patch</span>
            </summary>
            <pre>{file.lines.join("\n")}</pre>
          </details>
        ))}
      </div>
    </section>
  );
}
function DependencyLiveExecution({
  events,
  job,
  onRetry,
  retryDisabled
}: {
  events: DependencyJobEvent[];
  job: DependencyJob | null;
  onRetry: () => void;
  retryDisabled: boolean;
}) {
  const progress = dependencyJobProgress(job, events);
  const failed = progress.status === "failed";
  const completed = progress.status === "completed";
  const diagnosis = dependencyJobDiagnosis(job);
  const rollbackSummary = dependencyRollbackSummary(job);
  const terminalRef = React.useRef<HTMLDivElement | null>(null);
  const latestEvent = events[events.length - 1];
  const latestEventKey = latestEvent
    ? `${latestEvent.created_at || ""}:${latestEvent.type}:${latestEvent.phase || ""}:${latestEvent.message || ""}`
    : "empty";
  React.useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) return undefined;
    const frame = window.requestAnimationFrame(() => {
      terminal.scrollTop = terminal.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [latestEventKey, progress.status]);
  return (
    <section className="dependency-terminal-panel">
      <div className="dependency-panel-title">
        <div>
          <strong>Live Execution</strong>
          <p>{progress.label}</p>
        </div>
        {job ? <Badge tone={failed ? "red" : completed ? "green" : "blue"}>{titleCase(job.status)}</Badge> : null}
      </div>
      <div className={failed ? "dependency-progress failed" : completed ? "dependency-progress completed" : "dependency-progress"}>
        <div className="dependency-progress-track" aria-label={`Update progress ${progress.percent}%`}>
          <span style={{ width: `${progress.percent}%` }} />
        </div>
        <div className="dependency-progress-meta">
          <span>{progress.percent}%</span>
          <span>{progress.label}</span>
        </div>
      </div>
      {failed ? (
        <div className="dependency-job-resolution error">
          <strong>{diagnosis?.title || "Update did not complete."}</strong>
          <p>{diagnosis?.summary || "IACS could not complete this update."}</p>
          <p>{rollbackSummary}</p>
          {diagnosis?.affected_packages.length ? (
            <div className="dependency-recovery-pills">
              {diagnosis.affected_packages.map((name) => <Badge tone="amber" key={name}>{name}</Badge>)}
            </div>
          ) : null}
          {diagnosis?.actions.length ? (
            <div className="dependency-recovery-list">
              {diagnosis.actions.map((action, index) => (
                <div key={`${diagnosis.category}-action-${index}`}>
                  <CheckCircle2 size={14} />
                  <span>{action}</span>
                </div>
              ))}
            </div>
          ) : null}
          {diagnosis?.retry_recommendation ? <p>{diagnosis.retry_recommendation}</p> : null}
          {diagnosis?.command ? <code className="dependency-failed-command">{diagnosis.command}</code> : null}
          <button className="secondary-button" onClick={onRetry} disabled={retryDisabled} type="button">
            <RefreshCcw size={15} /> Retry Update
          </button>
        </div>
      ) : null}
      <div className="log-console dependency-terminal" ref={terminalRef}>
        {events.length ? events.map((event, index) => (
          <div className="log-line" key={`${event.created_at}-${event.type}-${index}`}>
            <time>{formatDependencyLogTime(event.created_at)}</time>
            <strong>{dependencyLogTypeLabel(event.type)}</strong>
            <code>{event.message || event.phase || ""}</code>
          </div>
        )) : (
          <div className="log-line">
            <time>now</time>
            <strong>queued</strong>
            <code>Waiting for job output...</code>
          </div>
        )}
      </div>
    </section>
  );
}
function DependencyUpdateConfirmModal({
  action,
  dependency,
  loading,
  onCancel,
  onConfirm
}: {
  action: DependencyConfirmAction;
  dependency: DependencyPackage;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const isApply = action.kind === "apply";
  return (
    <div className="modal-backdrop nested-modal-backdrop" role="presentation">
      <div className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="dependency-update-confirm-title">
        <div className="gate-confirm-title">
          <span className="gate-confirm-icon">
            {isApply ? <Play size={19} /> : <ShieldCheck size={19} />}
          </span>
          <div>
            <h2 id="dependency-update-confirm-title">{isApply ? "Proceed with dependency update?" : "Restore dependency backup?"}</h2>
            <p>
              {isApply
                ? `${dependency.package_name} will update from ${dependency.current_version || "unknown"} to ${dependency.latest_version || "the selected version"}. IACS will create an offline backup first, stream progress, verify the build, and roll back automatically if the update cannot be completed.`
                : `Restore backup ${action.backup.id}. IACS will validate the archive checksum, restore manifests, and run verification afterwards.`}
            </p>
          </div>
        </div>
        <div className="modal-actions">
          <button className="secondary-button" onClick={onCancel} disabled={loading} type="button">Cancel</button>
          <button className="primary-button" onClick={onConfirm} disabled={loading} type="button">
            {isApply ? <Play size={15} /> : <ShieldCheck size={15} />}
            {loading ? "Starting..." : isApply ? "Start Update" : "Restore Backup"}
          </button>
        </div>
      </div>
    </div>
  );
}
function DependencyStoragePanel({ storage, onChanged }: { storage: DependencyStorageStatus | null; onChanged: () => Promise<void> }) {
  const [mode, setMode] = React.useState(storage?.mode || "local");
  const [source, setSource] = React.useState(storage?.mount_source || "");
  const [options, setOptions] = React.useState("");
  const [optionsTouched, setOptionsTouched] = React.useState(false);
  const [minFree, setMinFree] = React.useState(String(storage?.min_free_bytes ?? 1073741824));
  const [retentionDays, setRetentionDays] = React.useState(String(storage?.retention_days || ""));
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  React.useEffect(() => {
    setMode(storage?.mode || "local");
    setSource(storage?.mount_source || "");
    setOptions("");
    setOptionsTouched(false);
    setMinFree(String(storage?.min_free_bytes ?? 1073741824));
    setRetentionDays(String(storage?.retention_days || ""));
  }, [storage]);
  const savedMountOptions = Boolean(storage?.mount_options_configured);
  const mountOptionsHint = mode === "local"
    ? "Remote mount options are not used for local backup storage."
    : savedMountOptions && !optionsTouched
      ? "Sensitive options are saved and hidden. Enter a new value to replace them."
      : optionsTouched && options.trim()
        ? "New mount options will replace the saved sensitive value."
        : optionsTouched
          ? "Saved mount options will be cleared when you save."
          : "Optional Docker mount options for this remote share.";
  const saveStorage = async () => {
    setSaving(true);
    setError("");
    try {
      const payload: {
        mode: string;
        mount_source: string;
        mount_options?: string;
        retention_days: string;
        min_free_bytes: number;
      } = {
        mode,
        mount_source: mode === "local" ? "" : source,
        retention_days: retentionDays,
        min_free_bytes: Number(minFree) || 0
      };
      if (mode === "local" || optionsTouched) {
        payload.mount_options = mode === "local" ? "" : options;
      }
      await integrationsApi.saveDependencyStorage(payload);
      await onChanged();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save storage configuration.");
    } finally {
      setSaving(false);
    }
  };
  const validate = async () => {
    setSaving(true);
    setError("");
    try {
      await integrationsApi.validateDependencyStorage();
      await onChanged();
    } catch (validateError) {
      setError(validateError instanceof Error ? validateError.message : "Unable to validate storage.");
    } finally {
      setSaving(false);
    }
  };
  return (
    <section className="card dependency-storage-panel">
      <div className="dependency-storage-summary">
        <div>
          <h2>Backup Storage</h2>
          <p>{storage?.detail || "Configure where offline update backups are stored."}</p>
        </div>
        <Badge tone={storage?.config_status === "pending_reboot" ? "amber" : storage?.ok ? "green" : "red"}>
          {storage?.config_status === "pending_reboot" ? "Reboot Required" : storage?.ok ? "Ready" : "Needs Attention"}
        </Badge>
      </div>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      <div className="dependency-storage-grid">
        <label className="field">
          <span>Mode</span>
          <select value={mode} onChange={(event) => setMode(event.target.value)}>
            <option value="local">Local bind mount</option>
            <option value="nfs">Host-mounted NFS path</option>
            <option value="samba">Host-mounted Samba/CIFS path</option>
          </select>
        </label>
        <label className="field">
          <span>Mount source</span>
          <input value={source} onChange={(event) => setSource(event.target.value)} placeholder={mode === "local" ? "./data/backend/dependency-update-backups" : mode === "nfs" ? "/mnt/iacs-update-backups" : "/mnt/iacs-update-backups"} disabled={mode === "local"} />
        </label>
        <label className="field">
          <span>Mount options</span>
          <input
            value={options}
            onChange={(event) => {
              setOptions(event.target.value);
              setOptionsTouched(true);
            }}
            placeholder={mode === "local" ? "not used for local mode" : mode === "samba" ? "username=iacs,password=...,vers=3.0,rw" : "addr=nas.local,rw"}
            disabled={mode === "local"}
          />
          <small className="field-hint">{mountOptionsHint}</small>
        </label>
        {mode !== "local" && savedMountOptions ? (
          <div className="dependency-storage-secret-controls">
            <button
              className="secondary-button"
              onClick={() => {
                setOptions("");
                setOptionsTouched(true);
              }}
              disabled={saving}
              type="button"
            >
              Clear Saved Options
            </button>
          </div>
        ) : null}
        <label className="field">
          <span>Minimum free bytes</span>
          <input value={minFree} onChange={(event) => setMinFree(event.target.value)} inputMode="numeric" />
        </label>
        <label className="field">
          <span>Retention days</span>
          <input value={retentionDays} onChange={(event) => setRetentionDays(event.target.value)} inputMode="numeric" placeholder="optional" />
        </label>
      </div>
      <div className="dependency-storage-meta">
        <span>Active root: <code>{storage?.backup_root || "/app/update-backups"}</code></span>
        <span>Free: <strong>{formatFileSize(storage?.free_bytes ?? 0)}</strong></span>
      </div>
      <div className="dependency-update-actions">
        <button className="secondary-button" onClick={validate} disabled={saving} type="button">
          <CheckCircle2 size={15} /> Validate
        </button>
        <button className="primary-button" onClick={saveStorage} disabled={saving} type="button">
          <Save size={15} /> Save Storage Config
        </button>
      </div>
      <p className="dependency-storage-note">Changing NFS/Samba storage writes a generated Compose override and requires a host reboot or full Compose recreation before the mount changes.</p>
    </section>
  );
}
export function riskTone(value: string | null | undefined): BadgeTone {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "breaking" || normalized === "red" || normalized === "error") return "red";
  if (normalized === "warning" || normalized === "unknown" || normalized === "amber") return "amber";
  if (normalized === "safe" || normalized === "green") return "green";
  return "gray";
}
