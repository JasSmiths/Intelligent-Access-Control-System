import { X, Zap } from "lucide-react";
import React from "react";

import { titleFromEntityId } from "../../lib/settings";
import type { HomeAssistantDiscovery, HomeAssistantMobileAppService } from "../../api/types";

import type { HomeAssistantInputBooleanAction } from "./types";

export function InputBooleanEntityPicker({
  entities,
  loading,
  selectedEntityIds,
  onChange
}: {
  entities: HomeAssistantDiscovery["input_boolean_entities"];
  loading: boolean;
  selectedEntityIds: string[];
  onChange: (entityIds: string[]) => void;
}) {
  const [draft, setDraft] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const [highlightedIndex, setHighlightedIndex] = React.useState(0);
  const [error, setError] = React.useState("");
  const selectedSet = React.useMemo(() => new Set(selectedEntityIds), [selectedEntityIds]);
  const suggestions = React.useMemo(() => {
    const query = draft.trim().toLowerCase();
    const candidates = entities.filter((entity) => !selectedSet.has(entity.entity_id));
    const filtered = query
      ? candidates.filter((entity) =>
          entity.entity_id.toLowerCase().includes(query) ||
          (entity.name ?? "").toLowerCase().includes(query)
        )
      : candidates;
    return filtered.slice(0, 8);
  }, [draft, entities, selectedSet]);

  React.useEffect(() => {
    setHighlightedIndex(0);
  }, [draft]);

  const addEntity = (entityId: string) => {
    const normalized = entityId.trim();
    if (!normalized) return;
    if (!normalized.startsWith("input_boolean.")) {
      setError("Use an input_boolean.* entity ID.");
      return;
    }
    setError("");
    if (!selectedSet.has(normalized)) {
      onChange([...selectedEntityIds, normalized]);
    }
    setDraft("");
    setOpen(false);
  };

  const acceptHighlighted = () => {
    const suggestion = suggestions[highlightedIndex] ?? suggestions[0];
    if (suggestion) {
      addEntity(suggestion.entity_id);
      return true;
    }
    return false;
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" && suggestions.length) {
      event.preventDefault();
      setOpen(true);
      setHighlightedIndex((current) => Math.min(current + 1, suggestions.length - 1));
      return;
    }
    if (event.key === "ArrowUp" && suggestions.length) {
      event.preventDefault();
      setOpen(true);
      setHighlightedIndex((current) => Math.max(current - 1, 0));
      return;
    }
    if ((event.key === "Tab" || event.key === " ") && suggestions.length && draft.trim()) {
      event.preventDefault();
      acceptHighlighted();
      return;
    }
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      if (!acceptHighlighted()) addEntity(draft);
    }
    if (event.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="input-boolean-picker">
      <div className="input-boolean-chip-list" aria-label="Selected input_boolean entities">
        {selectedEntityIds.length ? selectedEntityIds.map((entityId) => (
          <span className="input-boolean-chip" key={entityId}>
            {entityId}
            <button
              aria-label={`Remove ${entityId}`}
              onClick={() => onChange(selectedEntityIds.filter((selected) => selected !== entityId))}
              type="button"
            >
              <X size={13} />
            </button>
          </span>
        )) : <span className="muted-value">No input_booleans selected</span>}
      </div>
      <label className="field input-boolean-autocomplete">
        <span>Entity ID</span>
        <div className="field-control">
          <Zap size={17} />
          <input
            autoComplete="off"
            onBlur={() => window.setTimeout(() => setOpen(false), 120)}
            onChange={(event) => {
              setDraft(event.target.value);
              setOpen(true);
              setError("");
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={handleKeyDown}
            placeholder={loading ? "Loading input_booleans..." : "input_boolean.person_announcements"}
            value={draft}
          />
        </div>
        {open && suggestions.length ? (
          <div className="input-boolean-suggestions" role="listbox">
            {suggestions.map((entity, index) => (
              <button
                className={index === highlightedIndex ? "active" : ""}
                key={entity.entity_id}
                onMouseDown={(event) => {
                  event.preventDefault();
                  addEntity(entity.entity_id);
                }}
                role="option"
                type="button"
                aria-selected={index === highlightedIndex}
              >
                <strong>{entity.entity_id}</strong>
                <span>{entity.name || titleFromEntityId(entity.entity_id)}</span>
              </button>
            ))}
          </div>
        ) : null}
        {error ? <small className="input-boolean-error">{error}</small> : null}
      </label>
    </div>
  );
}

export function InputBooleanActionToggle({
  label,
  value,
  onChange
}: {
  label: string;
  value: HomeAssistantInputBooleanAction;
  onChange: (value: HomeAssistantInputBooleanAction) => void;
}) {
  return (
    <div className="input-boolean-action-toggle">
      <span>{label}</span>
      <div className="input-boolean-segmented" role="group" aria-label={label}>
        <button
          className={value === "turn_off" ? "active" : ""}
          onClick={() => onChange("turn_off")}
          type="button"
        >
          Off
        </button>
        <button
          className={value === "turn_on" ? "active" : ""}
          onClick={() => onChange("turn_on")}
          type="button"
        >
          On
        </button>
      </div>
    </div>
  );
}

export function MobileAppNotifySelectField({
  label,
  value,
  services,
  onChange
}: {
  label: string;
  value: string;
  services: HomeAssistantMobileAppService[];
  onChange: (value: string) => void;
}) {
  const hasCurrentValue = value && !services.some((service) => service.service_id === value);
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Select mobile app service</option>
        {hasCurrentValue ? <option value={value}>{value}</option> : null}
        {services.map((service) => (
          <option key={service.service_id} value={service.service_id}>
            {service.name ? `${service.name} - ${service.service_id}` : service.service_id}
          </option>
        ))}
      </select>
    </label>
  );
}
