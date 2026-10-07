import React from "react";
import { ApiError, isAbortError } from "../../api/client";
import { visitorPassesApi } from "../../api/visitorPasses";
import type { VisitorPass } from "../passes/types";
import { useDirectoryIdentities, useDirectoryPage } from "../directory/reads";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { reportsEventMatches } from "./invalidation";
export function useReportSubjects(query: string, open: boolean, personId: string, visitorPassId: string) {
  const peoplePage = useDirectoryPage("people", [], { query, active: true, enabled: open });
  const selectedPeople = useDirectoryIdentities("people", [], personId ? [personId] : []);
  const [passes, setPasses] = React.useState<VisitorPass[]>([]);
  const [searchError, setSearchError] = React.useState("");
  const [selectedError, setSelectedError] = React.useState<{ id: string; message: string } | null>(null);
  const [selectedPass, setSelectedPass] = React.useState<VisitorPass | null>(null);
  const [missingPassId, setMissingPassId] = React.useState("");
  const search = React.useCallback(async (signal: AbortSignal) => {
    setSearchError("");
    try { const result = await visitorPassesApi.search(query, { signal }); if (!signal.aborted) setPasses(result); }
    catch (error) { if (!signal.aborted && !isAbortError(error)) setSearchError(error instanceof Error ? error.message : "Visitor search unavailable"); throw error; }
  }, [query]);
  useCoordinatedRead(search, reportsEventMatches, open, query ? 150 : 0);
  const hydrate = React.useCallback(async (signal: AbortSignal) => {
    setMissingPassId(""); setSelectedError(null);
    try { const result = await visitorPassesApi.get(visitorPassId, { signal }); if (!signal.aborted) setSelectedPass(result); }
    catch (error) {
      if (!signal.aborted && !isAbortError(error)) {
        if (error instanceof ApiError && error.status === 404) { setSelectedPass(null); setMissingPassId(visitorPassId); }
        else setSelectedError({ id: visitorPassId, message: error instanceof Error ? error.message : "Selected visitor pass unavailable" });
      }
      throw error;
    }
  }, [visitorPassId]);
  useCoordinatedRead(hydrate, reportsEventMatches, Boolean(visitorPassId));
  return { people: peoplePage.items, selectedPeople: selectedPeople.items, missingPersonIds: selectedPeople.missingIds,
    visitorPasses: passes, selectedPass: selectedPass?.id === visitorPassId ? selectedPass : null, missingPassId: missingPassId === visitorPassId ? missingPassId : "",
    error: peoplePage.error || selectedPeople.error || searchError || (selectedError?.id === visitorPassId ? selectedError.message : "") };
}
