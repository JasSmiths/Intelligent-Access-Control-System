import { ArrowRight, Bot, Search, ShieldCheck, X } from "lucide-react";
import React from "react";
import type { InvestigationAnswer, InvestigationEvidence } from "./types";
import { dispatchStateExplanation, formatInvestigationTime, OutcomeLabel } from "./presentation";

const EXAMPLE_QUESTIONS = [
  "Why didn't the garage door open when I came home last night?",
  "Why did an automation run at 03:00?",
  "Which user or automation changed a device recently?"
];

function AnswerEvidence({
  evidence,
  linkedEpisodeId,
  onEpisodeSelect,
  timezone
}: {
  evidence: InvestigationEvidence;
  linkedEpisodeId?: string;
  onEpisodeSelect: (episodeId: string, evidenceId?: string) => void;
  timezone: string;
}) {
  return (
    <li id={`answer-evidence-${evidence.id}`}>
      <time dateTime={evidence.timestamp}>{formatInvestigationTime(evidence.timestamp, timezone, true)}</time>
      <span>
        <strong>{evidence.title}</strong>
        {evidence.description ? <small>{evidence.description}</small> : null}
      </span>
      {evidence.outcome ? <OutcomeLabel outcome={evidence.outcome} /> : null}
      {linkedEpisodeId ? <button className="investigation-evidence-link" onClick={() => onEpisodeSelect(linkedEpisodeId, evidence.id)} type="button">View exact evidence <ArrowRight aria-hidden="true" size={12} /></button> : null}
    </li>
  );
}

export function QuestionComposer({
  answer,
  error,
  loading,
  onClear,
  onEpisodeSelect,
  onSubmit
}: {
  answer: InvestigationAnswer | null;
  error: string;
  loading: boolean;
  onClear: () => void;
  onEpisodeSelect: (episodeId: string, evidenceId?: string) => void;
  onSubmit: (question: string) => void;
}) {
  const [question, setQuestion] = React.useState("");
  const evidence = answer?.evidence ?? [];
  const dispatch = answer ? dispatchStateExplanation(answer.dispatch_state) : null;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = question.trim();
    if (trimmed) onSubmit(trimmed);
  }

  return (
    <section className="investigation-question" aria-labelledby="investigation-question-title">
      <div className="investigation-question-heading">
        <div>
          <span className="investigation-eyebrow"><Search aria-hidden="true" size={14} /> Investigate</span>
          <h1 id="investigation-question-title">Ask what happened</h1>
          <p>Use plain language. Answers cite the authorised evidence IACS actually recorded.</p>
        </div>
        <span className="investigation-readonly"><ShieldCheck aria-hidden="true" size={14} /> Read-only</span>
      </div>
      <form className="investigation-question-form" onSubmit={submit}>
        <label className="sr-only" htmlFor="investigation-question-input">Investigation question</label>
        <textarea
          id="investigation-question-input"
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Why didn’t the main garage door open when I came home last night?"
          rows={2}
          value={question}
        />
        <button className="primary-button" disabled={loading || !question.trim()} type="submit">
          {loading ? "Investigating…" : "Investigate"}<ArrowRight aria-hidden="true" size={15} />
        </button>
      </form>
      {!answer && !loading ? (
        <div className="investigation-examples" aria-label="Example questions">
          {EXAMPLE_QUESTIONS.map((example) => (
            <button key={example} onClick={() => setQuestion(example)} type="button">{example}</button>
          ))}
        </div>
      ) : null}
      {error ? <div className="investigation-inline-error" role="alert">{error}</div> : null}
      {answer ? (
        <article className="investigation-answer" aria-live="polite">
          <div className="investigation-answer-header">
            <span><Bot aria-hidden="true" size={17} /> Investigation answer</span>
            <button aria-label="Close investigation answer" className="icon-button" onClick={onClear} type="button"><X size={15} /></button>
          </div>
          <p className="investigation-answer-copy">{answer.answer}</p>
          {answer.most_likely_reason ? (
            <div className="investigation-likely-reason">
              <span>Most likely reason</span>
              <strong>{answer.most_likely_reason}</strong>
            </div>
          ) : null}
          <div className="investigation-answer-meta">
            {answer.outcome ? <OutcomeLabel outcome={answer.outcome} /> : null}
            <span>Certainty: {answer.certainty || "unknown"}</span>
            <span>{answer.mode === "structured_fallback" ? "Structured evidence search" : "AI-assisted evidence search"}</span>
            <span>Timezone: {answer.site_timezone}</span>
          </div>
          {dispatch && answer.dispatch_state !== "not_applicable" ? (
            <div className={`investigation-answer-dispatch ${answer.dispatch_state || "unknown"}`}>
              <strong>{dispatch.label}</strong><span>{dispatch.detail}</span>
            </div>
          ) : null}
          {evidence.length ? (
            <div className="investigation-answer-evidence">
              <h2>Supporting evidence</h2>
              <ol>{evidence.map((item) => <AnswerEvidence evidence={item} key={item.id} linkedEpisodeId={item.episode_id || (answer.episodes?.length === 1 ? answer.episodes[0].episode_id : undefined)} onEpisodeSelect={onEpisodeSelect} timezone={answer.site_timezone} />)}</ol>
            </div>
          ) : null}
          {answer.missing_evidence?.length ? (
            <div className="investigation-missing-evidence">
              <strong>Evidence is incomplete</strong>
              <ul>{answer.missing_evidence.map((item) => <li key={item}>{item}</li>)}</ul>
            </div>
          ) : null}
        </article>
      ) : null}
    </section>
  );
}
