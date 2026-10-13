import {useCallback, useEffect, useRef, useState} from "react";
import {ApiError} from "./api";
import {getAssignmentResults} from "./classroomResultsApi";
import * as api from "./paperScanApi";
import {matchingErrorMessage, type ScanSnapshot} from "./paperScanState";

export type MatchingSnapshot = ScanSnapshot & {
  matching?: api.ScanMatching; grouping?: api.ScanGrouping; roster: api.MatchingStudent[];
};
const optional = async <T,>(read: Promise<T>, code: string): Promise<T | undefined> => {
  try {return await read;} catch (e) {if (e instanceof ApiError && e.code === code && e.status === 404) return undefined; throw e;}
};
async function readRoster(assignmentId: string, signal: AbortSignal): Promise<api.MatchingStudent[]> {
  const students: api.MatchingStudent[] = [];
  let total = 1;
  while (students.length < total) {
    const result = await getAssignmentResults(assignmentId, signal, {offset: students.length, limit: 100});
    total = result.participant_count ?? result.items.length;
    if (!result.items.length && students.length < total) throw new ApiError(409, "page_coverage_mismatch", "");
    students.push(...result.items.map(p => ({assignment_participant_id: p.assignment_participant_id, display_name: p.student_display_name})));
  }
  return students;
}
export function usePaperMatching(batchId: string, assignmentId: string) {
  const [data, setData] = useState<MatchingSnapshot>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const current = useRef<MatchingSnapshot | undefined>(undefined);
  const locked = useRef(false);
  const runner = useRef<() => Promise<void>>(async () => {});
  const refresh = useCallback(() => runner.current(), []);
  const commandRunner = useRef<(action: () => Promise<unknown>, processing: boolean) => Promise<void>>(async () => {});
  const mutate = useCallback((action: () => Promise<unknown>, processing = false) => commandRunner.current(action, processing), []);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight: Promise<void> | undefined;
    let roster: api.MatchingStudent[] | undefined;
    let commandProcessing = false;
    current.current = undefined; setData(undefined); setError(""); setNotice("");
    const load = (): Promise<void> => {
      if (controller.signal.aborted) return Promise.resolve();
      if (inFlight) return inFlight;
      clearTimeout(timer); setRefreshing(true);
      inFlight = (async () => {
        try {
          const batch = await api.getScanBatch(batchId, controller.signal);
          if (batch.assignment_id !== assignmentId) throw new ApiError(404, "scan_batch_not_found", "");
          const [artifacts, pages, matching, grouping, participants] = await Promise.all([
            api.listScanArtifacts(batchId, controller.signal), api.listScanPages(batchId, controller.signal),
            optional(api.readScanMatching(batchId, controller.signal), "matching_not_found"),
            optional(api.readScanGrouping(batchId, controller.signal), "grouping_not_found"),
            roster ? Promise.resolve(roster) : readRoster(assignmentId, controller.signal),
          ]);
          if (controller.signal.aborted) return;
          roster = participants;
          // Initialization is idempotent and creates a server draft, never confirmed membership.
          const draft = !grouping && batch.status === "matching_review_required" ? await api.initializeScanGrouping(batchId) : grouping;
          if (controller.signal.aborted) return;
          const next = {batch, artifacts: artifacts.items, pages: pages.items, matching, grouping: draft, roster: participants};
          const prev = current.current?.grouping;
          if (!locked.current && prev && draft && (prev.row_version !== draft.row_version || prev.grouping_revision !== draft.grouping_revision)) {
            setNotice(draft.grouping_status === "confirmed" ? "Распределение уже подтверждено. Загружены актуальные данные." : "Распределение изменилось. Мы загрузили актуальные данные.");
          }
          current.current = next; setData(next); setError("");
        } catch (e) {
          if (!controller.signal.aborted) {current.current = undefined; setData(undefined); setError(matchingErrorMessage(e));}
        } finally {
          inFlight = undefined;
          if (!controller.signal.aborted) {
            setRefreshing(false);
            if (!document.hidden && (current.current?.matching?.status === "running" || commandProcessing)) timer = setTimeout(() => void load(), 3000);
          }
        }
      })();
      return inFlight;
    };
    runner.current = load;
    commandRunner.current = async (action, processing) => {
      if (locked.current || controller.signal.aborted) return;
      locked.current = true; setBusy(true); setNotice(""); clearTimeout(timer);
      commandProcessing = processing;
      let failure: unknown;
      const pending = action().catch(e => {failure = e;});
      // Matching POST may hold the connection while durable work is running.
      // Reading alongside it exposes processing and polls without another start.
      if (processing) await load();
      await pending;
      commandProcessing = false;
      // Wait for a concurrent read and then make a fresh read after the command.
      if (inFlight) await inFlight;
      await load();
      if (!controller.signal.aborted) {
        if (failure) setNotice(matchingErrorMessage(failure));
        setBusy(false);
      }
      locked.current = false;
      if (current.current?.matching?.status !== "running") clearTimeout(timer);
    };
    const focus = () => {if (document.hidden) clearTimeout(timer); else if (!locked.current) void load();};
    document.addEventListener("visibilitychange", focus); window.addEventListener("focus", focus);
    void load();
    return () => {controller.abort(); clearTimeout(timer); document.removeEventListener("visibilitychange", focus); window.removeEventListener("focus", focus);};
  }, [batchId, assignmentId]);
  return {data, error, notice, busy, refreshing, refresh, mutate};
}
