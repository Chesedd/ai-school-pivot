import {useCallback, useEffect, useRef, useState} from "react";
import {ApiError} from "./api";
import * as scan from "./paperScanApi";
import * as api from "./paperReviewApi";
import {scanErrorMessage} from "./paperScanState";
import {transientChecking} from "./paperReviewState";

type Snapshot = {batch: scan.ScanBatch; grouping: scan.ScanGrouping; pages: scan.ScanPage[]; progress: api.CheckingProgress};
export function usePaperChecking(batchId: string, assignmentId: string) {
  const [data, setData] = useState<Snapshot>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const runner = useRef<() => Promise<void>>(async () => {});
  const command = useRef<(fn: () => Promise<unknown>) => Promise<void>>(async () => {});
  const refresh = useCallback(() => runner.current(), []);
  const mutate = useCallback((fn: () => Promise<unknown>) => command.current(fn), []);
  useEffect(() => {
    const controller = new AbortController();
    let snapshot: Snapshot | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight: Promise<void> | undefined;
    let locked = false;
    setData(undefined); setError(""); setNotice(""); setBusy(false);
    const load = (): Promise<void> => {
      if (controller.signal.aborted) return Promise.resolve();
      if (inFlight) return inFlight;
      clearTimeout(timer);
      inFlight = (async () => {
        try {
          if (!snapshot) {
            const batch = await scan.getScanBatch(batchId, controller.signal);
            if (batch.assignment_id !== assignmentId) throw new ApiError(404, "scan_batch_not_found", "");
            const [grouping, pages, progress] = await Promise.all([
              scan.readScanGrouping(batchId, controller.signal), scan.listScanPages(batchId, controller.signal), api.readChecking(batchId, controller.signal),
            ]);
            if (grouping.grouping_status !== "confirmed") throw new ApiError(409, "paper_batch_grouping_not_confirmed", "");
            snapshot = {batch, grouping, pages: pages.items, progress};
          } else {
            const progress = await api.readChecking(batchId, controller.signal);
            snapshot = {...snapshot, progress};
          }
          if (!controller.signal.aborted) {setData(snapshot); setError("");}
        } catch (e) {
          if (!controller.signal.aborted) {
            // Transient progress failures must not unmount an unsaved editor.
            // Authorization/object denials clear all previously actionable data.
            if (e instanceof ApiError && [401, 403, 404].includes(e.status)) snapshot = undefined;
            setData(snapshot); setError(scanErrorMessage(e));
          }
        } finally {
          inFlight = undefined;
          if (!controller.signal.aborted && !document.hidden && snapshot && transientChecking(snapshot.progress)) timer = setTimeout(() => void load(), 3000);
        }
      })();
      return inFlight;
    };
    runner.current = load;
    command.current = async fn => {
      if (locked || controller.signal.aborted) return;
      locked = true; setBusy(true); setNotice("");
      let failure: unknown;
      try {await fn();} catch (e) {failure = e;}
      if (inFlight) await inFlight;
      await load();
      if (!controller.signal.aborted) {if (failure) setNotice(scanErrorMessage(failure)); setBusy(false);}
      locked = false;
    };
    const focus = () => {if (document.hidden) clearTimeout(timer); else void load();};
    document.addEventListener("visibilitychange", focus); window.addEventListener("focus", focus);
    void load();
    return () => {controller.abort(); clearTimeout(timer); document.removeEventListener("visibilitychange", focus); window.removeEventListener("focus", focus);};
  }, [batchId, assignmentId]);
  return {data, error, notice, busy, refresh, mutate};
}
