import {afterEach, expect, it, vi} from "vitest";
import {createScanBatch, downloadScanArtifact, extractScanBatch, getScanBatch, listScanArtifacts, listScanBatches, listScanPages, uploadScanArtifact} from "./paperScanApi";
import {scanErrorMessage} from "./paperScanState";
afterEach(() => vi.unstubAllGlobals());
it("uses the real authorized endpoints, idempotency header and one-file multipart contract", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response("{}", {headers: {"Content-Type": "application/json"}})); vi.stubGlobal("fetch", fetcher);
  await createScanBatch("a/b", "Инструкция", "key");
  expect(fetcher.mock.calls[0][0]).toContain("/assignments/a%2Fb/scan-batches");
  expect(fetcher.mock.calls[0][1]).toMatchObject({credentials: "include", headers: {"Idempotency-Key": "key"}, body: JSON.stringify({instruction: "Инструкция"})});
  await listScanBatches("a"); await getScanBatch("b"); await listScanArtifacts("b"); await listScanPages("b"); await extractScanBatch("b");
  expect(fetcher.mock.calls.slice(1).map(x => x[0].split("/api")[1])).toEqual(["/assessment-core/assignments/a/scan-batches", "/assessment-core/scan-batches/b", "/assessment-core/scan-batches/b/artifacts", "/assessment-core/scan-batches/b/pages", "/assessment-core/scan-batches/b/extract"]);
  await uploadScanArtifact("b", new File(["%PDF-"], "work.pdf", {type: "application/pdf"}), 7);
  const init = fetcher.mock.calls.at(-1)![1]; expect(init.headers).toBeUndefined(); expect(Array.from(init.body.keys())).toEqual(["file", "upload_position"]); expect(init.body.get("upload_position")).toBe("7");
});
it("downloads bytes through the API client and preserves intentional 410 behavior", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response("%PDF-", {headers: {"Content-Type": "application/pdf"}})).mockResolvedValueOnce(new Response(JSON.stringify({detail: "scan_artifact_content_purged"}), {status: 410})); vi.stubGlobal("fetch", fetcher);
  const blob = await downloadScanArtifact("b", "f"); expect(await blob.text()).toBe("%PDF-");
  try {await downloadScanArtifact("b", "f"); throw new Error("expected 410");} catch (e) {expect(scanErrorMessage(e)).toBe("Исходный файл больше не хранится");}
});
