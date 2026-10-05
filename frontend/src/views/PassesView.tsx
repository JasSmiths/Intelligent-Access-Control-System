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

export function PassesView({ query, latestRealtime, refreshToken }: { query: string; latestRealtime: RealtimeMessage | null; refreshToken: number }) {
  const [passes, setPasses] = React.useState<VisitorPass[]>([]);
  const [filters, setFilters] = React.useState<Set<VisitorPassStatus>>(() => new Set(defaultVisitorPassFilters));
  const [modalPass, setModalPass] = React.useState<VisitorPass | null>(null);
  const [modalOpen, setModalOpen] = React.useState(false);
  const [detailPass, setDetailPass] = React.useState<VisitorPass | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [saved, setSaved] = React.useState("");
  const deferredQuery = React.useDeferredValue(query);
  const lastRefreshTokenRef = React.useRef(refreshToken);
  const loadSequenceRef = React.useRef(0);
  const loadAbortRef = React.useRef<AbortController | null>(null);

  const loadPasses = React.useCallback(async (options?: { showLoading?: boolean }) => {
    const sequence = loadSequenceRef.current + 1;
    loadSequenceRef.current = sequence;
    loadAbortRef.current?.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;
    const showLoading = options?.showLoading !== false;
    const params = new URLSearchParams();
    if (filters.size && filters.size < visitorPassStatuses.length) {
      filters.forEach((status) => params.append("status", status));
    }
    if (deferredQuery.trim()) params.set("q", deferredQuery.trim());
    const suffix = params.toString() ? `?${params.toString()}` : "";
    if (showLoading) setLoading(true);
    setError("");
    try {
      const rows = await api.get<VisitorPass[]>(`/api/v1/visitor-passes${suffix}`, { signal: controller.signal });
      if (loadSequenceRef.current !== sequence) return;
      setPasses(rows);
    } catch (loadError) {
      if (isAbortError(loadError)) return;
      if (loadSequenceRef.current !== sequence) return;
      setError(loadError instanceof Error ? loadError.message : "Unable to load Visitor Passes");
    } finally {
      if (loadSequenceRef.current === sequence) {
        if (showLoading) setLoading(false);
        if (loadAbortRef.current === controller) {
          loadAbortRef.current = null;
        }
      }
    }
  }, [deferredQuery, filters]);

  React.useEffect(() => () => {
    loadSequenceRef.current += 1;
    loadAbortRef.current?.abort();
  }, []);

  React.useEffect(() => {
    loadPasses().catch(() => undefined);
  }, [loadPasses]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    loadPasses({ showLoading: false }).catch(() => undefined);
  }, [loadPasses, refreshToken]);

  React.useEffect(() => {
    const latest = latestRealtime;
    if (!latest) return;
    if (isVisitorPassRealtimeEvent(latest)) {
      if (latest.type === "visitor_pass.deleted") {
        const deletedId = isRecord(latest.payload.visitor_pass) ? stringPayload(latest.payload.visitor_pass.id) : "";
        if (deletedId) {
          setPasses((current) => current.filter((item) => item.id !== deletedId));
          setDetailPass((current) => current?.id === deletedId ? null : current);
          setModalPass((current) => current?.id === deletedId ? null : current);
        }
        loadPasses({ showLoading: false }).catch(() => undefined);
        return;
      }
      const livePass = visitorPassFromRealtime(latest);
      if (livePass) {
        setPasses((current) => [livePass, ...current.filter((item) => item.id !== livePass.id)]);
        setDetailPass((current) => current?.id === livePass.id ? livePass : current);
        setModalPass((current) => current?.id === livePass.id ? livePass : current);
        return;
      }
      loadPasses({ showLoading: false }).catch(() => undefined);
    } else if (latest.type === "access_event.finalized") {
      loadPasses({ showLoading: false }).catch(() => undefined);
    }
  }, [latestRealtime, loadPasses]);

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

  const handlePassUpdated = React.useCallback(async (visitorPass: VisitorPass) => {
    setPasses((current) => [visitorPass, ...current.filter((item) => item.id !== visitorPass.id)]);
    setDetailPass(visitorPass);
    await loadPasses();
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
      await handlePassUpdated(cancelled);
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
      await loadPasses();
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
        <button className="secondary-button" onClick={() => loadPasses()} disabled={loading} type="button">
          <RefreshCw size={15} /> Refresh
        </button>
      </div>

      {error ? <ErrorState title="Visitor passes need attention" description={error} onRetry={() => void loadPasses()} retrying={loading} /> : null}
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
          latestRealtime={latestRealtime}
          visitorPass={detailPass}
        />
      ) : null}
    </section>
  );
}
