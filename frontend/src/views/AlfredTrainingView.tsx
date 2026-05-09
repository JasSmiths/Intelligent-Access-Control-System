import React from "react";
import { Bot, Check, Download, Link2, Loader2, RefreshCw, ThumbsDown, ThumbsUp, X } from "lucide-react";

import { api, Badge, titleCase, useSettings } from "../shared";

type AlfredTrainingSource = {
  kind: "user_feedback" | "self_learning" | "manual_training" | "seed" | "system" | string;
  label: string;
  detail: string | null;
  channel: string | null;
  actor_user_id: string | null;
};

type AlfredFeedbackRecord = {
  id: string;
  rating: "up" | "down" | string;
  source_channel: string;
  source?: AlfredTrainingSource | null;
  original_user_prompt: string;
  original_assistant_response: string;
  reason: string | null;
  ideal_answer: string | null;
  corrected_answer: string | null;
  status: string;
  lesson_id: string | null;
  created_at: string;
};

type AlfredLessonRecord = {
  id: string;
  scope: "user" | "site" | string;
  title: string;
  lesson: string;
  tags: string[];
  source_feedback_ids: string[];
  source?: AlfredTrainingSource | null;
  confidence: number;
  status: string;
  created_at: string;
  updated_at: string;
};

type AlfredEvalExample = {
  id: string;
  feedback_id: string | null;
  prompt: string;
  bad_answer: string | null;
  ideal_answer: string | null;
  corrected_answer: string | null;
  lesson: string | null;
  source?: AlfredTrainingSource | null;
  created_at: string;
};

type LessonDraft = {
  id: string;
  title: string;
  lesson: string;
  saving: boolean;
  error: string;
};

type LessonTab = "pending" | "learnt";

export function AlfredTrainingView({ refreshToken }: { refreshToken: number }) {
  const settings = useSettings("llm");
  const [feedback, setFeedback] = React.useState<AlfredFeedbackRecord[]>([]);
  const [lessons, setLessons] = React.useState<AlfredLessonRecord[]>([]);
  const [examples, setExamples] = React.useState<AlfredEvalExample[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [lessonDraft, setLessonDraft] = React.useState<LessonDraft | null>(null);
  const [lessonTab, setLessonTab] = React.useState<LessonTab>("pending");
  const [modeSaving, setModeSaving] = React.useState(false);
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const load = React.useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const [feedbackPayload, lessonPayload, examplePayload] = await Promise.all([
        api.get<{ feedback: AlfredFeedbackRecord[] }>("/api/v1/ai/training/feedback?limit=80"),
        api.get<{ lessons: AlfredLessonRecord[] }>("/api/v1/ai/training/lessons?limit=80"),
        api.get<{ examples: AlfredEvalExample[] }>("/api/v1/ai/training/eval-examples?limit=80")
      ]);
      setFeedback(feedbackPayload.feedback);
      setLessons(lessonPayload.lessons);
      setExamples(examplePayload.examples);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load Alfred training data.");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    load().catch(() => undefined);
    settings.reload().catch(() => undefined);
  }, [load, refreshToken, settings.reload]);

  const learningMode = String(settings.values.alfred_learning_mode || "review_then_learn");
  const pendingLessons = lessons.filter((lesson) => lesson.status === "pending");
  const learnedLessons = lessons.filter((lesson) => lesson.status !== "pending");
  const lessonById = React.useMemo(() => new Map(lessons.map((lesson) => [lesson.id, lesson])), [lessons]);
  const feedbackById = React.useMemo(() => new Map(feedback.map((item) => [item.id, item])), [feedback]);
  const examplesByFeedbackId = React.useMemo(() => {
    const grouped = new Map<string, AlfredEvalExample[]>();
    examples.forEach((example) => {
      if (!example.feedback_id) return;
      grouped.set(example.feedback_id, [...(grouped.get(example.feedback_id) ?? []), example]);
    });
    return grouped;
  }, [examples]);

  const lessonForFeedback = React.useCallback((item: AlfredFeedbackRecord) => {
    if (item.lesson_id && lessonById.has(item.lesson_id)) return lessonById.get(item.lesson_id) ?? null;
    return lessons.find((lesson) => lesson.source_feedback_ids.includes(item.id)) ?? null;
  }, [lessonById, lessons]);

  const feedbackForLesson = React.useCallback((lesson: AlfredLessonRecord) => {
    const linkedIds = new Set(lesson.source_feedback_ids);
    return feedback.filter((item) => item.lesson_id === lesson.id || linkedIds.has(item.id));
  }, [feedback]);
  const feedbackMatchesTab = React.useCallback((item: AlfredFeedbackRecord) => {
    const linkedLesson = lessonForFeedback(item);
    if (linkedLesson) return lessonTab === "pending" ? linkedLesson.status === "pending" : linkedLesson.status !== "pending";
    const unresolved = ["queued", "received", "analyzing", "analysis_failed"].includes(item.status);
    return lessonTab === "pending" ? unresolved : !unresolved;
  }, [lessonForFeedback, lessonTab]);
  const visibleLessons = lessonTab === "pending" ? pendingLessons : learnedLessons;
  const visibleFeedback = React.useMemo(
    () => feedback.filter((item) => feedbackMatchesTab(item)),
    [feedback, feedbackMatchesTab]
  );
  const visibleExamples = React.useMemo(
    () => examples.filter((example) => {
      if (!example.feedback_id) return lessonTab === "learnt";
      const sourceFeedback = feedbackById.get(example.feedback_id);
      return sourceFeedback ? feedbackMatchesTab(sourceFeedback) : lessonTab === "learnt";
    }),
    [examples, feedbackById, feedbackMatchesTab, lessonTab]
  );

  const saveLearningMode = async (nextMode: "review_then_learn" | "auto_learn") => {
    if (modeSaving || learningMode === nextMode) return;
    setModeSaving(true);
    try {
      await settings.save({ alfred_learning_mode: nextMode });
    } finally {
      setModeSaving(false);
    }
  };

  const reviewLesson = async (lesson: AlfredLessonRecord, decision: "approve" | "reject") => {
    const draft = lessonDraft?.id === lesson.id ? lessonDraft : null;
    setLessonDraft(draft ? { ...draft, saving: true, error: "" } : null);
    try {
      await api.post(`/api/v1/ai/training/lessons/${lesson.id}/review`, {
        decision,
        title: draft?.title,
        lesson: draft?.lesson
      });
      setLessonDraft(null);
      await load();
    } catch (reviewError) {
      const message = reviewError instanceof Error ? reviewError.message : "Unable to review lesson.";
      setLessonDraft(draft ? { ...draft, saving: false, error: message } : null);
    }
  };

  return (
    <div className="view-stack alfred-training-page">
      <div className="dashboard-intro">
        <div>
          <h1>Alfred Training</h1>
          <p>Review feedback, approve response lessons, and keep Alfred learning from real IACS conversations.</p>
        </div>
        <div className="alfred-training-actions">
          <a className="secondary-button" href="/api/v1/ai/training/eval-export">
            <Download size={15} />
            <span>Export JSONL</span>
          </a>
          <button className="secondary-button" onClick={() => load()} type="button">
            {loading ? <Loader2 className="spin" size={15} /> : <RefreshCw size={15} />}
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {error ? <div className="inline-error">{error}</div> : null}

      <section className="card alfred-training-mode-card">
        <div className="panel-header">
          <h2>Learning Mode</h2>
          <Badge tone={learningMode === "auto_learn" ? "green" : "blue"}>{titleCase(learningMode)}</Badge>
        </div>
        <div className="alfred-learning-mode">
          <button
            className={learningMode === "review_then_learn" ? "active" : ""}
            disabled={modeSaving || settings.loading}
            onClick={() => void saveLearningMode("review_then_learn")}
            type="button"
          >
            Review then learn
          </button>
          <button
            className={learningMode === "auto_learn" ? "active" : ""}
            disabled={modeSaving || settings.loading}
            onClick={() => void saveLearningMode("auto_learn")}
            type="button"
          >
            Auto learn
          </button>
        </div>
      </section>

      <div className="alfred-training-tabs" role="tablist" aria-label="Alfred training state">
        <button
          aria-selected={lessonTab === "pending"}
          className={lessonTab === "pending" ? "active" : ""}
          onClick={() => setLessonTab("pending")}
          role="tab"
          type="button"
        >
          <span>Pending</span>
          <strong>{pendingLessons.length}</strong>
        </button>
        <button
          aria-selected={lessonTab === "learnt"}
          className={lessonTab === "learnt" ? "active" : ""}
          onClick={() => setLessonTab("learnt")}
          role="tab"
          type="button"
        >
          <span>Learnt</span>
          <strong>{learnedLessons.length}</strong>
        </button>
      </div>

      <div className="alfred-training-grid">
        <section className="card alfred-training-panel">
          <div className="panel-header">
            <h2>Lessons</h2>
            <Badge tone={lessonTab === "pending" ? visibleLessons.length ? "amber" : "green" : "blue"}>{visibleLessons.length}</Badge>
          </div>
          <div className="alfred-training-list">
            {lessonTab === "pending" && (pendingLessons.length ? pendingLessons.map((lesson) => {
              const draft = lessonDraft?.id === lesson.id ? lessonDraft : null;
              const linkedFeedback = feedbackForLesson(lesson);
              return (
                <article className="alfred-training-item" key={lesson.id}>
                  <input
                    value={draft?.title ?? lesson.title}
                    onChange={(event) => setLessonDraft({
                      id: lesson.id,
                      title: event.target.value,
                      lesson: draft?.lesson ?? lesson.lesson,
                      saving: false,
                      error: ""
                    })}
                  />
                  <textarea
                    value={draft?.lesson ?? lesson.lesson}
                    onChange={(event) => setLessonDraft({
                      id: lesson.id,
                      title: draft?.title ?? lesson.title,
                      lesson: event.target.value,
                      saving: false,
                      error: ""
                    })}
                    rows={4}
                  />
                  <div className="alfred-training-meta">
                    <Badge tone={lesson.scope === "site" ? "purple" : "gray"}>{lesson.scope}</Badge>
                    <TrainingSourcePill source={lessonSourceForDisplay(lesson, linkedFeedback)} />
                    <span>{Math.round(lesson.confidence * 100)}% confidence</span>
                    <span>{linkedFeedback.length} feedback</span>
                  </div>
                  {draft?.error ? <small className="chat-feedback-error">{draft.error}</small> : null}
                  <div className="alfred-training-actions">
                    <button className="secondary-button" disabled={draft?.saving} onClick={() => void reviewLesson(lesson, "reject")} type="button">
                      <X size={14} />
                      <span>Reject</span>
                    </button>
                    <button className="primary-button" disabled={draft?.saving} onClick={() => void reviewLesson(lesson, "approve")} type="button">
                      {draft?.saving ? <Loader2 className="spin" size={14} /> : <Check size={14} />}
                      <span>Approve</span>
                    </button>
                  </div>
                </article>
              );
            }) : <EmptyTrainingState text="No pending lessons." />)}

            {lessonTab === "learnt" && (learnedLessons.length ? learnedLessons.slice(0, 24).map((lesson) => {
              const linkedFeedback = feedbackForLesson(lesson);
              const linkedEvalCount = linkedFeedback.reduce((count, item) => count + (examplesByFeedbackId.get(item.id)?.length ?? 0), 0);
              return (
                <article className="alfred-training-item compact learned" key={lesson.id}>
                  <div className="alfred-feedback-heading">
                    <Check size={14} />
                    <strong>{lesson.title}</strong>
                  </div>
                  <p>{lesson.lesson}</p>
                  <div className="alfred-training-meta">
                    <Badge tone={lesson.status === "active" ? "green" : "gray"}>{lesson.status}</Badge>
                    <TrainingSourcePill source={lessonSourceForDisplay(lesson, linkedFeedback)} />
                    <span>{linkedFeedback.length} feedback</span>
                    <span>{linkedEvalCount} eval</span>
                    <span>{formatShortDate(lesson.updated_at)}</span>
                  </div>
                </article>
              );
            }) : <EmptyTrainingState text="No learnt lessons yet." />)}
          </div>
        </section>

        <section className="card alfred-training-panel">
          <div className="panel-header">
            <h2>Feedback History</h2>
            <Badge tone="blue">{visibleFeedback.length}</Badge>
          </div>
          <div className="alfred-training-list">
            {visibleFeedback.length ? visibleFeedback.slice(0, 12).map((item) => {
              const linkedLesson = lessonForFeedback(item);
              const linkedExamples = examplesByFeedbackId.get(item.id) ?? [];
              return (
                <article className="alfred-training-item compact" key={item.id}>
                  <div className="alfred-feedback-heading">
                    {item.rating === "up" ? <ThumbsUp size={14} /> : <ThumbsDown size={14} />}
                    <strong>{item.original_user_prompt || "No prompt captured"}</strong>
                  </div>
                  <p>{item.reason || item.ideal_answer || item.corrected_answer || item.original_assistant_response}</p>
                  {linkedLesson ? (
                    <div className="alfred-linked-record">
                      <Link2 size={13} />
                      <span>{linkedLesson.title}</span>
                      <Badge tone={linkedLesson.status === "active" ? "green" : linkedLesson.status === "pending" ? "amber" : "gray"}>{linkedLesson.status}</Badge>
                    </div>
                  ) : null}
                  <div className="alfred-training-meta">
                    <Badge tone={item.rating === "up" ? "green" : "red"}>{item.rating}</Badge>
                    <TrainingSourcePill source={item.source} fallback={item.source_channel} />
                    <span>{linkedExamples.length} eval</span>
                    <span>{formatShortDate(item.created_at)}</span>
                    <span>#{shortId(item.id)}</span>
                  </div>
                </article>
              );
            }) : <EmptyTrainingState text={lessonTab === "pending" ? "No feedback linked to pending lessons." : "No learnt feedback yet."} />}
          </div>
        </section>

        <section className="card alfred-training-panel">
          <div className="panel-header">
            <h2>Eval Examples</h2>
            <Badge tone="purple">{visibleExamples.length}</Badge>
          </div>
          <div className="alfred-training-list">
            {visibleExamples.length ? visibleExamples.slice(0, 12).map((example) => {
              const sourceFeedback = example.feedback_id ? feedbackById.get(example.feedback_id) : null;
              const linkedLesson = sourceFeedback ? lessonForFeedback(sourceFeedback) : null;
              return (
                <article className="alfred-training-item compact" key={example.id}>
                  <div className="alfred-feedback-heading">
                    <Bot size={14} />
                    <strong>{example.prompt}</strong>
                  </div>
                  <p>{example.ideal_answer || example.corrected_answer || example.lesson || "No target answer captured."}</p>
                  {linkedLesson ? (
                    <div className="alfred-linked-record">
                      <Link2 size={13} />
                      <span>{linkedLesson.title}</span>
                      <Badge tone={linkedLesson.status === "active" ? "green" : linkedLesson.status === "pending" ? "amber" : "gray"}>{linkedLesson.status}</Badge>
                    </div>
                  ) : null}
                  <div className="alfred-training-meta">
                    <TrainingSourcePill source={example.source ?? sourceFeedback?.source} fallback={sourceFeedback?.source_channel} />
                    {sourceFeedback ? <span>Feedback #{shortId(sourceFeedback.id)}</span> : <span>Unlinked</span>}
                    <span>{formatShortDate(example.created_at)}</span>
                  </div>
                </article>
              );
            }) : <EmptyTrainingState text={lessonTab === "pending" ? "No eval examples linked to pending lessons." : "No learnt eval examples yet."} />}
          </div>
        </section>
      </div>
    </div>
  );
}

function shortId(value: string) {
  return value.replaceAll("-", "").slice(0, 8);
}

function lessonSourceForDisplay(lesson: AlfredLessonRecord, linkedFeedback: AlfredFeedbackRecord[]) {
  return lesson.source ?? linkedFeedback[0]?.source ?? null;
}

function TrainingSourcePill({ source, fallback }: { source?: AlfredTrainingSource | null; fallback?: string }) {
  const label = formatTrainingSource(source, fallback);
  const tone = trainingSourceTone(source);
  return (
    <span className={`alfred-training-source ${tone}`} title={label}>
      {label}
    </span>
  );
}

function trainingSourceTone(source?: AlfredTrainingSource | null) {
  if (source?.kind === "user_feedback" || source?.kind === "manual_training") return "user_feedback";
  if (source?.kind === "self_learning") return "self_learning";
  if (source?.kind === "seed") return "seed";
  return "system";
}

function formatTrainingSource(source?: AlfredTrainingSource | null, fallback?: string) {
  const fallbackLabel = fallback ? sourceChannelLabel(fallback) : "Unknown source";
  if (!source) return fallbackLabel;
  const label = source.label?.trim() || fallbackLabel;
  const detail = source.detail?.trim();
  return detail ? `${label} · ${detail}` : label;
}

function sourceChannelLabel(value: string) {
  const normalized = value.trim().toLowerCase();
  if (["dashboard", "ui", "web"].includes(normalized)) return "UI";
  if (normalized === "whatsapp" || normalized === "whatsapp_cloud") return "WhatsApp";
  if (normalized === "discord") return "Discord";
  return titleCase(value);
}

function EmptyTrainingState({ text }: { text: string }) {
  return <div className="empty-state compact">{text}</div>;
}

function formatShortDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}
