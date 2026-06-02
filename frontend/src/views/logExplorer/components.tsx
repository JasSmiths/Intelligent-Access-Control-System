import { Loader2, Save, Trash2 } from "lucide-react";
import React from "react";
import { createPortal } from "react-dom";

import type { LogRecord } from "./types";
import { exportRecords } from "./utils";

export function SaveFilterModal({
  error,
  onCancel,
  onSave
}: {
  error: string;
  onCancel: () => void;
  onSave: (name: string) => void;
}) {
  const [name, setName] = React.useState("");
  return createPortal(
    <div className="modal-backdrop" role="presentation">
      <form
        className="modal-card logs-save-filter-modal"
        onSubmit={(event) => {
          event.preventDefault();
          onSave(name.trim());
        }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="save-log-filter-title"
      >
        <div className="modal-header">
          <div>
            <h2 id="save-log-filter-title">Save Filter</h2>
            <p>Keep this source and filter set for quick access.</p>
          </div>
        </div>
        <label className="form-field">
          <span>Name</span>
          <input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Gate errors today" />
        </label>
        {error ? <div className="auth-error inline-error">{error}</div> : null}
        <div className="modal-actions">
          <button className="secondary-button" onClick={onCancel} type="button">Cancel</button>
          <button className="secondary-button active" type="submit"><Save size={15} /> Save Filter</button>
        </div>
      </form>
    </div>,
    document.body
  );
}

export function ClearLogsConfirmModal({
  error,
  loading,
  onCancel,
  onConfirm
}: {
  error: string;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return createPortal(
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="clear-logs-confirm-title">
        <div className="modal-header">
          <div className="gate-confirm-title">
            <span className="gate-confirm-icon danger">
              <Trash2 size={20} />
            </span>
            <div>
              <h2 id="clear-logs-confirm-title">Clear Logs?</h2>
              <p>Audit history, telemetry traces, artifacts, and file logs will be purged. People, vehicles, access events, and movement history are kept.</p>
            </div>
          </div>
        </div>
        {error ? <div className="auth-error inline-error">{error}</div> : null}
        <div className="modal-actions">
          <button className="secondary-button" disabled={loading} onClick={onCancel} type="button">
            Keep logs
          </button>
          <button className="danger-button" disabled={loading} onClick={onConfirm} type="button">
            {loading ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
            {loading ? "Clearing..." : "Clear Logs"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

export function exportVisible(records: LogRecord[], format: "json" | "csv") {
  exportRecords(records, format);
}
