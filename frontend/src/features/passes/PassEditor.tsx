import { useModalFocus } from "../../ui/useModalFocus";
import { useModalClose } from "../../ui/useModalClose";
import { useEditorDismiss } from "../../ui/useEditorDismiss";
import { Car, ChevronDown, ChevronRight, Save, Smartphone, UserPlus, X } from "lucide-react";
import React from "react";

import { api, createActionConfirmation } from "../../api/client";
import { formatDate, fromDateTimeLocal, scheduleDays, toDateTimeLocal } from "../../lib/format";



import type { VisitorPassType, VisitorPass } from "./types";

export const visitorPassTypes: VisitorPassType[] = ["one-time", "duration"];

const visitorPassWindowOptions = [30, 60, 90, 120, 180];

export function VisitorPassModal({
  mode,
  visitorPass,
  onClose: finishClose,
  onSaved: finishSaved
}: {
  mode: "create" | "edit";
  visitorPass: VisitorPass | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const modalRef = React.useRef<HTMLFormElement>(null);
  const onClose = useModalClose(modalRef, finishClose);
  const onSaved = useModalClose(modalRef, finishSaved);
  const [visitorName, setVisitorName] = React.useState(visitorPass?.visitor_name ?? "");
  const [passType, setPassType] = React.useState<VisitorPassType>(visitorPass?.pass_type ?? "one-time");
  const [numberPlate, setNumberPlate] = React.useState(visitorPass?.number_plate ?? "");
  const [visitorPhone, setVisitorPhone] = React.useState(visitorPass?.visitor_phone ? `+${visitorPass.visitor_phone}` : "");
  const [expectedTime, setExpectedTime] = React.useState(() => visitorPass ? new Date(visitorPass.expected_time) : nextVisitorPassDate());
  const [windowMinutes, setWindowMinutes] = React.useState(visitorPass?.window_minutes ?? 30);
  const [validFrom, setValidFrom] = React.useState(() => visitorPass?.valid_from ? new Date(visitorPass.valid_from) : visitorPass ? new Date(visitorPass.window_start) : nextVisitorPassDate());
  const [validUntil, setValidUntil] = React.useState(() => {
    if (visitorPass?.valid_until) return new Date(visitorPass.valid_until);
    const start = visitorPass ? new Date(visitorPass.window_end) : nextVisitorPassDate();
    start.setHours(start.getHours() + 2);
    return start;
  });
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const draftSnapshot = JSON.stringify([visitorName, passType, visitorPhone, numberPlate, expectedTime.toISOString(), windowMinutes, validFrom.toISOString(), validUntil.toISOString()]);
  const initialDraft = React.useRef(draftSnapshot);
  const requestClose = useEditorDismiss(onClose, draftSnapshot !== initialDraft.current, submitting, "visitor pass changes");
  useModalFocus(modalRef, true, requestClose);
  const isDuration = passType === "duration";
  const updateDateFromInput = (value: string, setter: (date: Date) => void) => {
    const iso = fromDateTimeLocal(value);
    if (!iso) return;
    const next = new Date(iso);
    if (!Number.isNaN(next.getTime())) setter(next);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    const normalizedPlate = numberPlate.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
    if (isDuration && (mode === "create" || visitorPass?.pass_type !== "duration" || Boolean(visitorPass?.number_plate)) && !normalizedPlate) {
      setError("Duration passes need a number plate.");
      return;
    }
    if (isDuration && validUntil <= validFrom) {
      setError("Duration pass end time must be after the start time.");
      return;
    }
    setSubmitting(true);
    const payload: Record<string, unknown> & { visitor_name: string } = isDuration
      ? {
        visitor_name: visitorName.trim(),
        pass_type: passType,
        visitor_phone: visitorPhone.trim() || null,
        expected_time: validFrom.toISOString(),
        window_minutes: windowMinutes,
        valid_from: validFrom.toISOString(),
        valid_until: validUntil.toISOString()
      }
      : {
        visitor_name: visitorName.trim(),
        pass_type: passType,
        visitor_phone: null,
        expected_time: expectedTime.toISOString(),
        window_minutes: windowMinutes,
        valid_from: null,
        valid_until: null
      };
    if (mode === "create" || normalizedPlate !== (visitorPass?.number_plate ?? "")) {
      payload.number_plate = normalizedPlate || null;
    }
    try {
      if (mode === "edit" && visitorPass) {
        const confirmation = await createActionConfirmation("visitor_pass.update", { ...payload, pass_id: visitorPass.id }, {
          target_entity: "VisitorPass",
          target_id: visitorPass.id,
          target_label: payload.visitor_name,
          reason: "Update visitor pass"
        });
        await api.patch<VisitorPass>(`/api/v1/visitor-passes/${visitorPass.id}`, {
          ...payload,
          confirmation_token: confirmation.confirmation_token
        });
      } else {
        const confirmation = await createActionConfirmation("visitor_pass.create", payload, {
          target_entity: "VisitorPass",
          target_label: payload.visitor_name,
          reason: "Create visitor pass"
        });
        await api.post<VisitorPass>("/api/v1/visitor-passes", {
          ...payload,
          confirmation_token: confirmation.confirmation_token
        });
      }
      await onSaved();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save Visitor Pass");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose(); }}>
      <form ref={modalRef} role="dialog" aria-modal="true" aria-label="Visitor pass" className="modal-card visitor-pass-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Visitor Pass" : "New Visitor Pass"}</h2>
            <p>{isDuration ? `${formatDate(validFrom.toISOString())} to ${formatDate(validUntil.toISOString())}` : `${formatDate(expectedTime.toISOString())} · +/- ${windowMinutes} minutes`}</p>
          </div>
          <button className="icon-button" onClick={requestClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}

        <label className="field">
          <span>Visitor name</span>
          <div className="field-control">
            <UserPlus size={17} />
            <input value={visitorName} onChange={(event) => setVisitorName(event.target.value)} required />
          </div>
        </label>

        <label className="field">
          <span>Number plate{isDuration ? "" : " (optional)"}</span>
          <div className="field-control">
            <Car size={17} />
            <input value={numberPlate} onChange={(event) => setNumberPlate(event.target.value.toUpperCase())} required={isDuration && (mode === "create" || visitorPass?.pass_type !== "duration" || Boolean(visitorPass?.number_plate))} autoCapitalize="characters" />
          </div>
        </label>

        <div className="visitor-pass-window-select visitor-pass-type-select">
          <span>Pass type</span>
          <div>
            {visitorPassTypes.map((type) => (
              <button
                aria-pressed={passType === type}
                className={passType === type ? "active" : ""}
                key={type}
                onClick={() => setPassType(type)}
                type="button"
              >
                {type === "one-time" ? "One-time" : "Duration"}
              </button>
            ))}
          </div>
        </div>

        {isDuration ? (
          <section className="visitor-pass-duration-fields">
            <label className="field">
              <span>Visitor phone</span>
              <div className="field-control">
                <Smartphone size={17} />
                <input
                  autoComplete="tel"
                  onChange={(event) => setVisitorPhone(event.target.value)}
                  placeholder="+447700900123"
                  type="tel"
                  value={visitorPhone}
                />
              </div>
            </label>
            <label className="field compact-field">
              <span>Valid from</span>
              <input
                onChange={(event) => updateDateFromInput(event.target.value, setValidFrom)}
                required
                type="datetime-local"
                value={toDateTimeLocal(validFrom.toISOString())}
              />
            </label>
            <label className="field compact-field">
              <span>Valid until</span>
              <input
                onChange={(event) => updateDateFromInput(event.target.value, setValidUntil)}
                required
                type="datetime-local"
                value={toDateTimeLocal(validUntil.toISOString())}
              />
            </label>
          </section>
        ) : (
          <>
            <VisitorDateTimePicker value={expectedTime} onChange={setExpectedTime} />

            <div className="visitor-pass-window-select">
              <span>Time Window</span>
              <div>
                {visitorPassWindowOptions.map((minutes) => (
                  <button
                    aria-pressed={windowMinutes === minutes}
                    className={windowMinutes === minutes ? "active" : ""}
                    key={minutes}
                    onClick={() => setWindowMinutes(minutes)}
                    type="button"
                  >
                    +/- {minutes}m
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        <div className="modal-actions">
          <button className="secondary-button" onClick={requestClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            <Save size={16} />
            {submitting ? "Saving..." : mode === "edit" ? "Save Pass" : "Create Pass"}
          </button>
        </div>
      </form>
    </div>
  );
}

function VisitorDateTimePicker({ value, onChange }: { value: Date; onChange: (value: Date) => void }) {
  const [visibleMonth, setVisibleMonth] = React.useState(() => new Date(value.getFullYear(), value.getMonth(), 1));
  const days = visitorCalendarDays(visibleMonth);
  const selectedKey = visitorDateKey(value);
  const timeValue = `${String(value.getHours()).padStart(2, "0")}:${String(value.getMinutes()).padStart(2, "0")}`;
  const timeOptions = visitorTimeOptions(timeValue);

  const setDay = (day: Date) => {
    const next = new Date(day);
    next.setHours(value.getHours(), value.getMinutes(), 0, 0);
    onChange(next);
  };

  const setTime = (time: string) => {
    const [hour, minute] = time.split(":").map(Number);
    const next = new Date(value);
    next.setHours(hour, minute, 0, 0);
    onChange(next);
  };

  return (
    <section className="visitor-date-picker">
      <div className="visitor-date-picker-head">
        <button aria-label="Previous month" className="icon-button" onClick={() => setVisibleMonth(new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() - 1, 1))} type="button">
          <ChevronDown className="rotate-90" size={15} />
        </button>
        <strong>{visibleMonth.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</strong>
        <button aria-label="Next month" className="icon-button" onClick={() => setVisibleMonth(new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() + 1, 1))} type="button">
          <ChevronRight size={15} />
        </button>
      </div>
      <div className="visitor-calendar-grid">
        {scheduleDays.map((day) => <span key={day}>{day.slice(0, 2)}</span>)}
        {days.map((day) => (
          <button
            className={`${day.getMonth() === visibleMonth.getMonth() ? "" : "muted"} ${visitorDateKey(day) === selectedKey ? "active" : ""}`}
            key={day.toISOString()}
            onClick={() => setDay(day)}
            type="button"
          >
            {day.getDate()}
          </button>
        ))}
      </div>
      <label className="field">
        <span>Expected time</span>
        <select value={timeValue} onChange={(event) => setTime(event.target.value)}>
          {timeOptions.map((time) => (
            <option key={time} value={time}>{time}</option>
          ))}
        </select>
      </label>
    </section>
  );
}

function nextVisitorPassDate() {
  const next = new Date();
  next.setMinutes(Math.ceil(next.getMinutes() / 15) * 15, 0, 0);
  if (next.getMinutes() === 60) {
    next.setHours(next.getHours() + 1, 0, 0, 0);
  }
  return next;
}

function visitorDateKey(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

function visitorCalendarDays(month: Date) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const mondayOffset = (first.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(first.getDate() - mondayOffset);
  return Array.from({ length: 42 }, (_, index) => {
    const day = new Date(start);
    day.setDate(start.getDate() + index);
    return day;
  });
}

function visitorTimeOptions(selected?: string) {
  const options = Array.from({ length: 96 }, (_, index) => {
    const minutes = index * 15;
    return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  });
  if (selected && !options.includes(selected)) {
    options.push(selected);
    options.sort();
  }
  return options;
}
