import {useCallback, useEffect, useRef, useState} from "react";
import {ApiError} from "./api";
import {getScanBatch, listScanArtifacts, listScanPages} from "./paperScanApi";
import {needsPolling, scanErrorMessage, type ScanSnapshot} from "./paperScanState";

export function usePaperScanBatch(batchId: string, assignmentId: string, processing = false) {
  const [data, setData] = useState<ScanSnapshot>();
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const current = useRef<ScanSnapshot | undefined>(undefined);
  const processingRef = useRef(processing);
  processingRef.current = processing;
  const stopPolling = useRef<() => void>(() => {});
  const runner = useRef<(force?: boolean) => Promise<void>>(async () => {});
  const refresh = useCallback((force = false) => runner.current(force), []);
  useEffect(() => {
    const controller = new AbortController();
    let inFlight: Promise<void> | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    stopPolling.current = () => clearTimeout(timer);
    setData(undefined); current.current = undefined; setError("");
    const load = (force = false): Promise<void> => {
      if (controller.signal.aborted) return Promise.resolve();
      if (inFlight) return force ? inFlight.then(() => load()) : inFlight;
      clearTimeout(timer);
      setRefreshing(true);
      inFlight = (async () => {
        try {
          const batch = await getScanBatch(batchId, controller.signal);
          if (batch.assignment_id !== assignmentId) throw new ApiError(404, "scan_batch_not_found", "");
          const [artifacts, pages] = await Promise.all([
            listScanArtifacts(batchId, controller.signal), listScanPages(batchId, controller.signal),
          ]);
          if (!controller.signal.aborted) {
            const next = {batch, artifacts: artifacts.items, pages: pages.items};
            current.current = next; setData(next); setError("");
          }
        } catch (e) {
          if (!controller.signal.aborted) {
            setError(scanErrorMessage(e));
            // Stale academic data must not remain actionable after a failed read.
            current.current = undefined; setData(undefined);
          }
        } finally {
          inFlight = undefined;
          if (!controller.signal.aborted) {
            setRefreshing(false);
            if ((processingRef.current || current.current && needsPolling(current.current)) && !document.hidden) timer = setTimeout(() => void load(), 3000);
          }
        }
      })();
      return inFlight;
    };
    runner.current = load;
    const focus = () => {if (!document.hidden) void load(); else clearTimeout(timer);};
    document.addEventListener("visibilitychange", focus);
    window.addEventListener("focus", focus);
    void load();
    return () => {
      controller.abort(); clearTimeout(timer);
      document.removeEventListener("visibilitychange", focus); window.removeEventListener("focus", focus);
    };
  }, [batchId, assignmentId]);
  useEffect(() => {
    if (processing) void refresh();
    else if (!current.current || !needsPolling(current.current)) stopPolling.current();
  }, [processing, refresh]);
  return {data, error, refreshing, refresh};
}
