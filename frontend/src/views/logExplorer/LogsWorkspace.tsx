import React from "react";

import {
  api,
  createActionConfirmation
} from "../../shared";
import {
  ClearLogsConfirmModal,
  exportVisible,
  SaveFilterModal
} from "./components";
import { defaultLogsFilters } from "./constants";
import { LogsControls } from "./LogsControls";
import {
  sortRecordsByNewest,
  useLogsData,
  useSelectedLogRecord,
  useTraceDetail
} from "./hooks";
import { NarrativeFeed } from "./NarrativeFeed";
import {
  LogsFilters,
  LogSourceKey,
  SavedLogsFilter
} from "./types";
import {
  applyLocalFilters,
  loadSavedFilters,
  saveSavedFilters
} from "./utils";

export function LogsWorkspace({
  refreshToken
}: {
  refreshToken: number;
}) {
  const [activeSource, setActiveSource] = React.useState<LogSourceKey>("all");
  const [filters, setFilters] = React.useState<LogsFilters>(defaultLogsFilters);
  const [notice, setNotice] = React.useState("");
  const [clearing, setClearing] = React.useState(false);
  const [clearConfirmOpen, setClearConfirmOpen] = React.useState(false);
  const [clearError, setClearError] = React.useState("");
  const [savedFilters, setSavedFilters] = React.useState<SavedLogsFilter[]>(() => loadSavedFilters());
  const [saveFilterOpen, setSaveFilterOpen] = React.useState(false);
  const [saveFilterError, setSaveFilterError] = React.useState("");

  const resetKey = React.useMemo(() => [
    activeSource,
    filters.query,
    filters.timeRange,
    filters.level,
    filters.status,
    filters.actor,
    filters.subject,
    filters.slowOnly ? "slow" : "all"
  ].join("|"), [activeSource, filters.actor, filters.level, filters.query, filters.slowOnly, filters.status, filters.subject, filters.timeRange]);

  const {
    auditRecords,
    clearRecords,
    error,
    hasNextCursor,
    loading,
    loadingMore,
    loadMore,
    refresh,
    setError,
    summary,
    traceRecords
  } = useLogsData({ activeSource, filters, refreshToken });

  const combinedRecords = React.useMemo(() => {
    return sortRecordsByNewest([...traceRecords, ...auditRecords]);
  }, [auditRecords, traceRecords]);

  const visibleRecords = React.useMemo(
    () => applyLocalFilters(combinedRecords, filters, activeSource),
    [activeSource, combinedRecords, filters]
  );

  const { selectedId, selectedRecord, setSelectedId } = useSelectedLogRecord(visibleRecords, resetKey);
  const { clearTraceDetails, selectedTraceDetail } = useTraceDetail(selectedRecord, resetKey);

  React.useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(""), 4500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  async function clearLogs() {
    setClearing(true);
    setError("");
    setClearError("");
    setNotice("");
    try {
      const payload = { scope: "full" };
      const confirmation = await createActionConfirmation("telemetry.purge", payload, {
        target_entity: "Telemetry",
        target_label: "All logs",
        reason: "Clear telemetry, audit history, artifacts, and file logs"
      });
      await api.delete("/api/v1/telemetry/purge", {
        scope: "full",
        confirmation_token: confirmation.confirmation_token
      });
      clearRecords();
      clearTraceDetails();
      await refresh();
      setClearConfirmOpen(false);
      setNotice("Logs cleared. Audit history, telemetry traces, artifacts, and file logs were purged.");
    } catch (clearError) {
      const message = clearError instanceof Error ? clearError.message : "Unable to clear logs";
      setClearError(message);
      setError(message);
    } finally {
      setClearing(false);
    }
  }

  function saveCurrentFilter(name: string) {
    if (!name) {
      setSaveFilterError("Give this filter a name.");
      return;
    }
    const next: SavedLogsFilter = {
      id: `filter_${Date.now()}`,
      name,
      source: activeSource,
      filters
    };
    const updated = [next, ...savedFilters.filter((filter) => filter.name.toLowerCase() !== name.toLowerCase())].slice(0, 12);
    setSavedFilters(updated);
    saveSavedFilters(updated);
    setSaveFilterError("");
    setSaveFilterOpen(false);
    setNotice(`Saved filter "${name}".`);
  }

  return (
    <section className="view-stack logs-workspace">
      <LogsControls
        activeSource={activeSource}
        clearing={clearing}
        filters={filters}
        onApplySaved={(filter) => {
          setActiveSource(filter.source === "live" ? "all" : filter.source);
          setFilters({ ...defaultLogsFilters, ...filter.filters });
        }}
        onChange={setFilters}
        onClear={() => {
          setClearError("");
          setClearConfirmOpen(true);
        }}
        onExportCsv={() => exportVisible(visibleRecords, "csv")}
        onExportJson={() => exportVisible(visibleRecords, "json")}
        onRefresh={() => refresh().catch(() => undefined)}
        onReset={() => setFilters(defaultLogsFilters)}
        onSave={() => {
          setSaveFilterError("");
          setSaveFilterOpen(true);
        }}
        onSourceChange={setActiveSource}
        savedFilters={savedFilters}
        storage={summary?.storage || null}
        summary={summary}
      />

      {error ? <div className="error-banner">{error}</div> : null}
      {notice ? <div className="success-banner">{notice}</div> : null}

      <div className="logs-main-grid logs-ledger-layout">
        <NarrativeFeed
          loading={loading}
          loadingMore={loadingMore}
          nextCursor={hasNextCursor}
          onLoadMore={() => loadMore().catch(() => undefined)}
          onSelect={(record) => setSelectedId((current) => current === record.id ? null : record.id)}
          records={visibleRecords}
          selectedId={selectedRecord?.id || selectedId}
          traceDetail={selectedTraceDetail}
        />
      </div>

      {clearConfirmOpen ? (
        <ClearLogsConfirmModal
          error={clearError}
          loading={clearing}
          onCancel={() => {
            if (clearing) return;
            setClearConfirmOpen(false);
            setClearError("");
          }}
          onConfirm={clearLogs}
        />
      ) : null}

      {saveFilterOpen ? (
        <SaveFilterModal
          error={saveFilterError}
          onCancel={() => setSaveFilterOpen(false)}
          onSave={saveCurrentFilter}
        />
      ) : null}
    </section>
  );
}
