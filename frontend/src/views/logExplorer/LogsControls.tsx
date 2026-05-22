import {
  CalendarDays,
  Download,
  FileJson,
  Filter,
  Gauge,
  Layers3,
  Loader2,
  RefreshCcw,
  RotateCcw,
  Save,
  Search,
  SlidersHorizontal,
  Terminal,
  Trash2,
  X
} from "lucide-react";
import type React from "react";

import {
  levelOptions,
  sourceTabs,
  statusOptions,
  timeRangeOptions
} from "./constants";
import type {
  LogSourceKey,
  LogsFilters,
  SavedLogsFilter,
  TelemetryStorageSummary,
  TelemetrySummary
} from "./types";
import {
  formatLogMegabytes,
  metricFromSummary
} from "./utils";

export function LogsControls({
  activeSource,
  clearing,
  filters,
  savedFilters,
  storage,
  summary,
  onApplySaved,
  onChange,
  onClear,
  onExportCsv,
  onExportJson,
  onRefresh,
  onReset,
  onSave,
  onSourceChange
}: {
  activeSource: LogSourceKey;
  clearing: boolean;
  filters: LogsFilters;
  savedFilters: SavedLogsFilter[];
  storage: TelemetryStorageSummary | null;
  summary: TelemetrySummary | null;
  onApplySaved: (filter: SavedLogsFilter) => void;
  onChange: (filters: LogsFilters) => void;
  onClear: () => void;
  onExportCsv: () => void;
  onExportJson: () => void;
  onRefresh: () => void;
  onReset: () => void;
  onSave: () => void;
  onSourceChange: (source: LogSourceKey) => void;
}) {
  const update = (patch: Partial<LogsFilters>) => onChange({ ...filters, ...patch });
  const storageSummary = storage || summary?.storage || null;
  const storageLabel = storageSummary ? formatLogMegabytes(storageSummary.total_size_bytes) : "...";
  const storageWidth = storageSummary?.total_size_bytes ? "52%" : "12%";

  const countForSource = (source: LogSourceKey) => {
    return metricFromSummary(summary, source);
  };
  const visibleTotal = countForSource(activeSource);

  return (
    <>
      <div className="logs-command-bar">
        <div className="logs-command-title">
          <Terminal size={20} />
          <div>
            <h1>Logs</h1>
            <span>Fast ledger of when it happened, what happened, and why. {visibleTotal.toLocaleString()} records in scope.</span>
          </div>
        </div>
        <div className="logs-command-actions">
          <div className="logs-storage-meter" aria-label="Telemetry storage usage">
            <span>Storage</span>
            <strong>{storageLabel}</strong>
            <i><b style={{ width: storageWidth }} /></i>
          </div>
          <button className="secondary-button" onClick={onRefresh} type="button">
            <RefreshCcw size={15} /> Refresh
          </button>
          <div className="logs-export-group">
            <button className="secondary-button" onClick={onExportCsv} type="button">
              <Download size={15} /> CSV
            </button>
            <button className="secondary-button" onClick={onExportJson} type="button">
              <FileJson size={15} /> JSON
            </button>
          </div>
          <button className="danger-button" disabled={clearing} onClick={onClear} type="button">
            {clearing ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
            {clearing ? "Clearing..." : "Clear Logs"}
          </button>
        </div>
      </div>

      <div className="logs-filter-bar">
        <label className="search logs-search">
          <Search size={16} />
          <input
            value={filters.query}
            onChange={(event) => update({ query: event.target.value })}
            placeholder='Search person, plate, trace, "slow webhook"...'
          />
        </label>
        <SelectFilter
          icon={Layers3}
          label="Source"
          value={activeSource}
          onChange={(value) => onSourceChange(value as LogSourceKey)}
          options={sourceTabs.map((tab) => ({
            value: tab.key,
            label: `${tab.label} (${countForSource(tab.key).toLocaleString()})`
          }))}
        />
        <SelectFilter
          icon={CalendarDays}
          label="Time"
          value={filters.timeRange}
          onChange={(value) => update({ timeRange: value })}
          options={timeRangeOptions}
        />
        <SelectFilter
          icon={SlidersHorizontal}
          label="Level"
          value={filters.level}
          onChange={(value) => update({ level: value })}
          options={levelOptions}
        />
        <SelectFilter
          icon={Filter}
          label="Status"
          value={filters.status}
          onChange={(value) => update({ status: value })}
          options={statusOptions}
        />
        <label className={filters.slowOnly ? "logs-toggle active" : "logs-toggle"} title="Show only high-latency or warning events">
          <input
            checked={filters.slowOnly}
            onChange={(event) => update({ slowOnly: event.target.checked })}
            type="checkbox"
          />
          <Gauge size={14} />
          <span>Slow only</span>
        </label>
        <select
          aria-label="Saved filters"
          className="logs-saved-filter"
          onChange={(event) => {
            const selected = savedFilters.find((filter) => filter.id === event.target.value);
            if (selected) onApplySaved(selected);
            event.currentTarget.value = "";
          }}
          value=""
        >
          <option value="">Saved Filters</option>
          {savedFilters.map((filter) => (
            <option key={filter.id} value={filter.id}>{filter.name}</option>
          ))}
        </select>
        <button className="secondary-button" onClick={onSave} type="button">
          <Save size={15} /> Save
        </button>
        <button className="secondary-button" onClick={onReset} type="button">
          <RotateCcw size={15} /> Reset
        </button>
        {filters.actor ? (
          <button className="logs-filter-chip" onClick={() => update({ actor: "" })} type="button">
            Actor: {filters.actor}
            <X size={13} />
          </button>
        ) : null}
        {filters.subject ? (
          <button className="logs-filter-chip" onClick={() => update({ subject: "" })} type="button">
            Target: {filters.subject}
            <X size={13} />
          </button>
        ) : null}
      </div>
    </>
  );
}

function SelectFilter({
  icon: Icon,
  label,
  value,
  options,
  onChange
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <label className="logs-select-filter">
      <Icon size={14} />
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}
