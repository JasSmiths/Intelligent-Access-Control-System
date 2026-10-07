import React from "react";
import { reportsApi, type ReportPreviewRequest, type ReportPreviewResponse } from "../../api/reports";
import { quickRanges, type QuickRange } from "./model";

export function useReportRange() {
  const [range, setRange] = React.useState<QuickRange>("7d");
  const [endInput, setEndInput] = React.useState("");
  const [startInput, setStartInput] = React.useState("");
  const [siteTimezone, setSiteTimezone] = React.useState("");
  const [rangeRevision, setRangeRevision] = React.useState(0);
  const [isLoadingContext, setIsLoadingContext] = React.useState(true);
  const [contextError, setContextError] = React.useState<string | null>(null);
  const [folds, setFolds] = React.useState<Pick<ReportPreviewRequest, "period_start_fold" | "period_end_fold">>({});
  React.useEffect(() => {
    if (range === "custom") { setIsLoadingContext(false); return; }
    const controller = new AbortController();
    setIsLoadingContext(true);
    setContextError(null);
    reportsApi.context({ signal: controller.signal }).then((context) => {
      if (controller.signal.aborted) return;
      setSiteTimezone(context.site_timezone);
      const hours = quickRanges.find((item) => item.value === range)!.hours;
      setEndInput(context.now);
      setStartInput(new Date(new Date(context.now).getTime() - hours * 3_600_000).toISOString());
      setFolds({});
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setContextError(error instanceof Error ? error.message : "Site time could not be loaded.");
    }).finally(() => {
      if (!controller.signal.aborted) setIsLoadingContext(false);
    });
    return () => controller.abort();
  }, [range, rangeRevision]);

  return { range, setRange, endInput, setEndInput, startInput, setStartInput, siteTimezone, setSiteTimezone,
    setRangeRevision, isLoadingContext, setIsLoadingContext, contextError, folds, setFolds };
}

export function useReportPreview(previewRequest: ReportPreviewRequest | null) {
  const [preview, setPreview] = React.useState<{ request: ReportPreviewRequest; result: ReportPreviewResponse } | null>(null);
  const [previewError, setPreviewError] = React.useState<{ request: ReportPreviewRequest; message: string } | null>(null);
  React.useEffect(() => {
    if (!previewRequest) return;
    const controller = new AbortController();
    reportsApi.preview(previewRequest, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setPreview({ request: previewRequest, result }); })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setPreviewError({ request: previewRequest, message: error instanceof Error ? error.message : "Report preview failed." });
      });
    return () => controller.abort();
  }, [previewRequest]);
  const currentPreview = preview?.request === previewRequest ? preview.result : null;
  const currentPreviewError = previewError?.request === previewRequest ? previewError.message : null;
  const previewPending = Boolean(previewRequest && !currentPreview && !currentPreviewError);
  return { currentPreview, currentPreviewError, previewPending };
}
