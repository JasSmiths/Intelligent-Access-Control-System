import { Filter, RotateCcw, Search, SlidersHorizontal } from "lucide-react";
import type { FilterOption, InvestigationFilterCatalog, InvestigationQuery } from "./types";
import { activeFilterCount } from "./query";

function FilterSelect({
  id,
  label,
  options,
  value,
  onChange
}: {
  id: string;
  label: string;
  options: FilterOption[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="investigation-filter-field" htmlFor={id}>
      <span>{label}</span>
      <select id={id} onChange={(event) => onChange(event.target.value)} value={value}>
        <option value="">All</option>
        {options.map((option) => <option key={`${option.value}:${option.label}`} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}

export function InvestigationFilters({
  catalog,
  onChange,
  onReset,
  query,
  timezone
}: {
  catalog: InvestigationFilterCatalog;
  onChange: (patch: Partial<InvestigationQuery>) => void;
  onReset: () => void;
  query: InvestigationQuery;
  timezone: string;
}) {
  const activeCount = activeFilterCount(query);
  return (
    <section className="investigation-filter-shell" aria-labelledby="investigation-filter-title">
      <div className="investigation-filter-heading">
        <div>
          <h2 id="investigation-filter-title"><Filter aria-hidden="true" size={15} /> Find activity</h2>
          <span>Times are interpreted in <strong>{timezone}</strong>.</span>
        </div>
        {activeCount ? <button className="investigation-reset" onClick={onReset} type="button"><RotateCcw aria-hidden="true" size={13} /> Reset {activeCount} {activeCount === 1 ? "filter" : "filters"}</button> : null}
      </div>
      <div className="investigation-filter-primary">
        <label className="investigation-search-field" htmlFor="investigation-free-text">
          <Search aria-hidden="true" size={15} />
          <span className="sr-only">Search recorded activity</span>
          <input
            id="investigation-free-text"
            onChange={(event) => onChange({ q: event.target.value })}
            placeholder="Search activity, reason codes or IDs"
            type="search"
            value={query.q}
          />
        </label>
        <label className="investigation-filter-field" htmlFor="investigation-range">
          <span>When</span>
          <select id="investigation-range" onChange={(event) => onChange({ range: event.target.value as InvestigationQuery["range"] })} value={query.range}>
            <option value="today">Today</option>
            <option value="yesterday">Yesterday</option>
            <option value="24h">Last 24 hours</option>
            <option value="7d">Last 7 days</option>
            <option value="custom">Custom range</option>
          </select>
        </label>
        <FilterSelect id="investigation-device" label="Device" onChange={(device) => onChange({ device })} options={catalog.devices} value={query.device} />
        <FilterSelect id="investigation-outcome" label="Outcome" onChange={(outcome) => onChange({ outcome })} options={catalog.outcomes} value={query.outcome} />
      </div>
      {query.range === "custom" ? (
        <div className="investigation-custom-range">
          <label htmlFor="investigation-from"><span>From</span><input id="investigation-from" onChange={(event) => onChange({ from: event.target.value })} type="datetime-local" value={query.from} /></label>
          <label htmlFor="investigation-to"><span>To</span><input id="investigation-to" onChange={(event) => onChange({ to: event.target.value })} type="datetime-local" value={query.to} /></label>
          <small>Custom times use {timezone}; no browser-timezone conversion is applied.</small>
        </div>
      ) : null}
      <details className="investigation-advanced-filters">
        <summary><SlidersHorizontal aria-hidden="true" size={14} /> More filters</summary>
        <div className="investigation-filter-grid">
          <FilterSelect id="investigation-automation" label="Automation or rule" onChange={(automation) => onChange({ automation })} options={catalog.automations} value={query.automation} />
          <FilterSelect id="investigation-schedule" label="Schedule" onChange={(schedule) => onChange({ schedule })} options={catalog.schedules} value={query.schedule} />
          <FilterSelect id="investigation-integration" label="Integration" onChange={(integration) => onChange({ integration })} options={catalog.integrations} value={query.integration} />
          <FilterSelect id="investigation-category" label="Category" onChange={(category) => onChange({ category })} options={catalog.categories} value={query.category} />
          <FilterSelect id="investigation-severity" label="Severity" onChange={(severity) => onChange({ severity })} options={catalog.severities} value={query.severity} />
          <FilterSelect id="investigation-actor" label="User or actor" onChange={(actor) => onChange({ actor })} options={catalog.actors} value={query.actor} />
          <FilterSelect id="investigation-trigger" label="Trigger source" onChange={(trigger) => onChange({ trigger })} options={catalog.triggers} value={query.trigger} />
          <label className="investigation-filter-field" htmlFor="investigation-trace">
            <span>Trace or execution ID</span>
            <input id="investigation-trace" onChange={(event) => onChange({ trace: event.target.value })} placeholder="Exact identifier" value={query.trace} />
          </label>
        </div>
        <label className="investigation-routine-toggle">
          <input checked={query.includeRoutine} onChange={(event) => onChange({ includeRoutine: event.target.checked })} type="checkbox" />
          <span>Include high-volume routine activity</span>
        </label>
      </details>
    </section>
  );
}
