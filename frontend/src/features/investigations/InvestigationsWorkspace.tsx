import { AlertTriangle, LockKeyhole, RefreshCw } from "lucide-react";
import React from "react";
import { ActivityTimeline } from "./ActivityTimeline";
import { InvestigationFilters } from "./InvestigationFilters";
import { InvestigationOverview } from "./InvestigationOverview";
import { QuestionComposer } from "./QuestionComposer";
import { useEpisodeDetails, useInvestigationData, useInvestigationQueryState, useQuestionInvestigation } from "./hooks";
import { activeFilterCount } from "./query";
import { EMPTY_FILTER_CATALOG } from "./types";

export function InvestigationsWorkspace({ refreshToken }: { refreshToken: number }) {
  const { query, committedQuery, updateQuery, resetQuery } = useInvestigationQueryState();
  const data = useInvestigationData(committedQuery, refreshToken);
  const timezone = data.page?.site_timezone || data.overview?.site_timezone || data.filters?.site_timezone || "UTC";
  const question = useQuestionInvestigation(committedQuery, timezone);
  const episodeDetails = useEpisodeDetails();
  const [selectedEpisodeId, setSelectedEpisodeId] = React.useState<string>();
  const [focusedEvidenceId, setFocusedEvidenceId] = React.useState<string>();
  const hasFilters = activeFilterCount(committedQuery) > 0;

  function selectEpisode(episodeId: string, evidenceId?: string) {
    setSelectedEpisodeId(episodeId);
    setFocusedEvidenceId(evidenceId);
    episodeDetails.load(episodeId);
  }

  if (data.forbidden) {
    return (
      <section className="view-stack investigations-page">
        <div className="investigation-permission-state" role="alert">
          <LockKeyhole aria-hidden="true" size={28} />
          <h1>Activity investigations require administrator access</h1>
          <p>This page can contain sensitive audit and device evidence. Ask an administrator if you need access.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="view-stack investigations-page">
      <QuestionComposer answer={question.answer} error={question.error} loading={question.loading} onClear={question.clear} onEpisodeSelect={selectEpisode} onSubmit={question.submit} />

      {data.error ? (
        <div className="investigation-page-error" role="alert">
          <AlertTriangle aria-hidden="true" size={16} />
          <span><strong>Some investigation data could not be loaded.</strong>{data.error}</span>
          <button aria-label="Retry by refreshing this page" className="secondary-button" onClick={() => window.location.reload()} type="button"><RefreshCw aria-hidden="true" size={14} /> Retry</button>
        </div>
      ) : null}

      {!hasFilters && data.overview ? <InvestigationOverview onSelect={selectEpisode} overview={data.overview} /> : null}

      <InvestigationFilters catalog={data.filters ?? EMPTY_FILTER_CATALOG} onChange={updateQuery} onReset={resetQuery} query={query} timezone={timezone} />

      <ActivityTimeline
        detailErrors={episodeDetails.errors}
        details={episodeDetails.details}
        focusedEvidenceId={focusedEvidenceId}
        hasFilters={hasFilters}
        items={data.items}
        loading={data.loading}
        loadingDetailIds={episodeDetails.loadingIds}
        loadingMore={data.loadingMore}
        nextCursor={data.page?.next_cursor ?? null}
        onLoadDetail={episodeDetails.load}
        onLoadMore={() => void data.loadMore()}
        partial={Boolean(data.page?.partial)}
        requestedEpisodeId={selectedEpisodeId}
        timezone={timezone}
      />
    </section>
  );
}
