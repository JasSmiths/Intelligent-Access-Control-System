import React from "react";
import { reportsApi, type ReportRequest, type ReportExportResponse } from "../../api/reports";

export function useReportActions() {
  const [loadedReport, setLoadedReport] = React.useState<ReportExportResponse | null>(null);
  const [isExportingReport, setIsExportingReport] = React.useState(false);
  const [isLoadingReportId, setIsLoadingReportId] = React.useState(false);
  const [reportActionError, setReportActionError] = React.useState<string | null>(null);
  const actionController = React.useRef<AbortController | null>(null);
  React.useEffect(() => () => actionController.current?.abort(), []);
  const cancelReportAction = React.useCallback(() => {
    actionController.current?.abort(); actionController.current = null;
    setIsLoadingReportId(false); setIsExportingReport(false);
  }, []);
  const downloadReportPdf = React.useCallback((downloadUrl: string) => {
    const anchor = document.createElement("a");
    anchor.href = downloadUrl; anchor.download = "";
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
  }, []);
  const loadSavedReport = React.useCallback(async (reportId: string) => {
    cancelReportAction();
    const controller = new AbortController(); actionController.current = controller;
    setIsLoadingReportId(true); setReportActionError(null);
    try {
      const response = await reportsApi.load(reportId, { signal: controller.signal });
      if (controller.signal.aborted) return null;
      setLoadedReport(response);
      return response;
    } catch (error) {
      if (!controller.signal.aborted) setReportActionError(error instanceof Error ? error.message : "Report could not be loaded.");
      return null;
    } finally {
      if (actionController.current === controller) { actionController.current = null; setIsLoadingReportId(false); }
    }
  }, [cancelReportAction]);
  const exportReport = React.useCallback(async (request: ReportRequest) => {
    if (actionController.current) return null;
    const controller = new AbortController(); actionController.current = controller;
    setIsExportingReport(true); setReportActionError(null);
    try {
      const response = await reportsApi.export(request, { signal: controller.signal });
      if (controller.signal.aborted) return null;
      setLoadedReport(response); downloadReportPdf(response.download_url);
      return response;
    } catch (error) {
      if (!controller.signal.aborted) setReportActionError(error instanceof Error ? error.message : "Report could not be exported.");
      return null;
    } finally {
      if (actionController.current === controller) { actionController.current = null; setIsExportingReport(false); }
    }
  }, [downloadReportPdf]);
  return { loadedReport, setLoadedReport, isExportingReport, isLoadingReportId, reportActionError, setReportActionError,
    cancelReportAction, downloadReportPdf, loadSavedReport, exportReport };
}
