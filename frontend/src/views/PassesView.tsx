import { useCoordinatedRead } from "../lib/useCoordinatedRead";
import { ReadSignalsContext } from "../lib/readSignals";
import { auditResource } from "../lib/realtime";
import { ClipboardPaste, Plus, RefreshCw } from "lucide-react";
import React from "react";

import { api, createActionConfirmation, isAbortError } from "../api/client";
import { isRecord, stringPayload } from "../lib/format";
import { EmptyState, ErrorState, LoadingState } from "../ui/primitives";
import type { RealtimeMessage } from "../api/types";



import type { VisitorPassStatus, VisitorPass } from "../features/passes/types";
import { visitorPassStatuses, defaultVisitorPassFilters, visitorPassMatches, visitorPassMatchesStatus, isVisitorPassRealtimeEvent, visitorPassFromRealtime } from "../features/passes/model";
import { PassFilterBar, VisitorPassCard } from "../features/passes/components";
import { VisitorPassDetailsModal } from "../features/passes/PassDetails";
import { VisitorPassModal } from "../features/passes/PassEditor";

export function PassesView({ query }: { query: string }) {
  const [passes, setPasses] = React.useState<VisitorPass[]>([]);
  const [filters, setFilters] = React.useState<Set<VisitorPassStatus>>(() => new Set(defaultVisitorPassFilters));
  const [modalPass, setModalPass] = React.useState<VisitorPass | null>(null);
  const [modalOpen, setModalOpen] = React.useState(false);
  const [detailPass, setDetailPass] = React.useState<VisitorPass | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [saved, setSaved] = React.useState("");
  const deferredQuery = React.useDeferredValue(query);
  const signals = React.useContext(ReadSignalsContext);
  const read = React.useCallback(async (signal: AbortSignal) => {
    const params = new URLSearchParams();
    if (filters.size && filters.size < visitorPassStatuses.length) filters.forEach((status) => params.append("status", status));
    if (deferredQuery.trim()) params.set("q", deferredQuery.trim());
    const suffix = params.toString() ? `?${params}` : "";
    setLoading(true); setError("");
    try { const rows = await api.get<VisitorPass[]>(`/api/v1/visitor-passes${suffix}`, { signal }); if (!signal.aborted) setPasses(rows); }
    catch (failure) { if (!signal.aborted && !isAbortError(failure)) setError(failure instanceof Error ? failure.message : "Unable to load Visitor Passes"); throw failure; }
    finally { if (!signal.aborted) setLoading(false); }
  }, [deferredQuery, filters]);
  const eventMatches = React.useCallback((event: RealtimeMessage) => event.type === "access_event.finalized" || event.type === "access_event.updated" || auditResource(event) === "person" || isVisitorPassRealtimeEvent(event) && (event.type === "visitor_pass.deleted" || !visitorPassFromRealtime(event)), []);
  const loadPasses = useCoordinatedRead(read, eventMatches);
  React.useLayoutEffect(() => signals?.subscribe((signal) => {
    if (signal.type !== "realtime" || !isVisitorPassRealtimeEvent(signal.message)) return;
    const latest = signal.message;
    if (latest.type === "visitor_pass.deleted") {
      const id = isRecord(latest.payload.visitor_pass) ? stringPayload(latest.payload.visitor_pass.id) : "";
      if (id) { setPasses((current) => current.filter((item) => item.id !== id)); setDetailPass((current) => current?.id === id ? null : current); setModalPass((current) => current?.id === id ? null : current); }
    } else {
      const livePass = visitorPassFromRealtime(latest);
      if (livePass) { setPasses((current) => [livePass, ...current.filter((item) => item.id !== livePass.id)]); setDetailPass((current) => current?.id === livePass.id ? livePass : current); setModalPass((current) => current?.id === livePass.id ? livePass : current); }
    }
  }), [signals]);

  const openCreate = () => {
    setModalPass(null);
    setModalOpen(true);
  };

  const openEdit = (visitorPass: VisitorPass) => {
    setModalPass(visitorPass);
    setModalOpen(true);
  };

  const openDetails = (visitorPass: VisitorPass) => {
    setDetailPass(visitorPass);
  };

  const closeModal = () => {
    setModalOpen(false);
    setModalPass(null);
  };

  const handlePassUpdated = React.useCallback(async (visitorPass: VisitorPass, receipt = "Pass saved") => {
    setPasses((current) => [visitorPass, ...current.filter((item) => item.id !== visitorPass.id)]);
    setDetailPass(visitorPass);
    try { await loadPasses(); } catch { setError(`${receipt}, but the list could not be refreshed. Refresh to retry the read.`); }
  }, [loadPasses]);

  const cancelPass = async (visitorPass: VisitorPass): Promise<VisitorPass | null> => {
    setError("");
    try {
      const payload = { reason: "Cancelled from dashboard" };
      const confirmation = await createActionConfirmation("visitor_pass.cancel", { ...payload, pass_id: visitorPass.id }, {
        target_entity: "VisitorPass",
        target_id: visitorPass.id,
        target_label: visitorPass.visitor_name,
        reason: "Cancel visitor pass"
      });
      const cancelled = await api.post<VisitorPass>(`/api/v1/visitor-passes/${visitorPass.id}/cancel`, {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      await handlePassUpdated(cancelled, "Pass cancelled");
      return cancelled;
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : "Unable to cancel Visitor Pass");
      return null;
    }
  };

  const deletePass = async (visitorPass: VisitorPass): Promise<boolean> => {
    setError("");
    try {
      const confirmationPayload = { pass_id: visitorPass.id };
      const confirmation = await createActionConfirmation("visitor_pass.delete", confirmationPayload, {
        target_entity: "VisitorPass",
        target_id: visitorPass.id,
        target_label: visitorPass.visitor_name,
        reason: "Delete visitor pass"
      });
      await api.delete(`/api/v1/visitor-passes/${visitorPass.id}`, {
        confirmation_token: confirmation.confirmation_token
      });
      setPasses((current) => current.filter((item) => item.id !== visitorPass.id));
      try { await loadPasses(); } catch { setError("Pass deleted, but the list could not be refreshed. Refresh to retry the read."); }
      return true;
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete Visitor Pass");
      return false;
    }
  };

  const { visiblePasses, counts } = React.useMemo(() => {
    const nextCounts: Record<VisitorPassStatus, number> = { active: 0, scheduled: 0, used: 0, expired: 0, cancelled: 0 };
    const nextVisible: VisitorPass[] = [];
    for (const visitorPass of passes) {
      nextCounts[visitorPass.status] += 1;
      if (visitorPassMatchesStatus(visitorPass, filters) && visitorPassMatches(visitorPass, deferredQuery)) {
        nextVisible.push(visitorPass);
      }
    }
    return { visiblePasses: nextVisible, counts: nextCounts };
  }, [deferredQuery, filters, passes]);

  return (
    <section className="view-stack passes-page">
      <div className="users-hero passes-hero card">
        <div>
          <span className="eyebrow">Anticipatory Access</span>
          <h1>Passes</h1>
          <p>One-shot visitor windows for unknown vehicles, with captured arrival, vehicle, and duration telemetry.</p>
        </div>
        <button className="primary-button" onClick={openCreate} type="button">
          <Plus size={17} /> Visitor Pass
        </button>
      </div>

      <div className="passes-toolbar card">
        <PassFilterBar counts={counts} filters={filters} onChange={setFilters} />
        <button className="secondary-button" onClick={() => void loadPasses().catch(() => undefined)} disabled={loading} type="button">
          <RefreshCw size={15} /> Refresh
        </button>
      </div>

      {error ? <ErrorState title="Visitor passes need attention" description={error} onRetry={() => void loadPasses().catch(() => undefined)} retrying={loading} /> : null}
      {saved ? <div className="success-note" role="status">{saved}</div> : null}

      {loading ? (
        <LoadingState label="Loading Visitor Passes" />
      ) : visiblePasses.length ? (
        <div className="visitor-pass-grid">
          <>
            {visiblePasses.map((visitorPass) => (
              <VisitorPassCard
                key={visitorPass.id}
                onOpen={openDetails}
                visitorPass={visitorPass}
              />
            ))}
          </>
        </div>
      ) : !error ? (
        <div className="card passes-empty-card">
          <EmptyState icon={ClipboardPaste} label="No Visitor Passes match this view" description="Change the search or status filters to find a pass, or prepare a new visitor's arrival." action={<button className="secondary-button" onClick={openCreate} type="button"><Plus size={15} /> Create visitor pass</button>} />
        </div>
      ) : null}

      {modalOpen ? (
        <VisitorPassModal
          mode={modalPass ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            closeModal();
            setSaved("Pass saved.");
            try { await loadPasses(); } catch { setError("Pass saved, but the list could not be refreshed. Refresh to see the latest data."); }
          }}
          visitorPass={modalPass}
        />
      ) : null}

      {detailPass ? (
        <VisitorPassDetailsModal
          onCancel={cancelPass}
          onClose={() => setDetailPass(null)}
          onDelete={deletePass}
          onEdit={(visitorPass) => {
            setDetailPass(null);
            openEdit(visitorPass);
          }}
          visitorPass={detailPass}
        />
      ) : null}
    </section>
  );
}
