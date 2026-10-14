import {afterEach, expect, it, vi} from "vitest";
import * as api from "./paperPublicationApi";
import {ApiError} from "./api";
import {scanErrorMessage} from "./paperScanState";

afterEach(() => vi.unstubAllGlobals());
const success = () => new Response(JSON.stringify({id: "publication-A", active: false}));
const path = (call: unknown[]) => String(call[0]).replace(/^https?:\/\/[^/]+/, "");
it("sends an explicit approved revision and command header", async () => {
  const fetch = vi.fn().mockResolvedValue(success()); vi.stubGlobal("fetch", fetch);
  const command = api.preparePublish("paper/1", "approved-R1"); await command.run();
  expect(path(fetch.mock.calls[0])).toBe("/api/assessment-core/paper-submissions/paper%2F1/publications");
  expect(fetch.mock.calls[0][1].headers["Idempotency-Key"]).toBe(command.key);
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({approved_review_revision_id: "approved-R1"});
});
it("suppresses duplicate clicks and retains a definitive outcome", async () => {
  let resolve!: (response: Response) => void;
  const fetch = vi.fn().mockReturnValue(new Promise<Response>(r => {resolve = r;})); vi.stubGlobal("fetch", fetch);
  const command = api.preparePublish("p", "R1"); const first = command.run();
  expect(command.run()).toBe(first); expect(fetch).toHaveBeenCalledTimes(1);
  resolve(success()); await first;
  expect(command.run()).toBe(first); expect(fetch).toHaveBeenCalledTimes(1);
});
it.each(["network", "server"])("keeps key and frozen payload for uncertain %s retries", async failure => {
  const fetch = vi.fn();
  if (failure === "network") fetch.mockRejectedValueOnce(new TypeError("Network"));
  else fetch.mockResolvedValueOnce(new Response("{}", {status: 503}));
  fetch.mockResolvedValueOnce(success()); vi.stubGlobal("fetch", fetch);
  const entries = [{paper_submission_id: "p", approved_review_revision_id: "R1"}];
  const command = api.prepareSelectedPublish("b", entries);
  await expect(command.run()).rejects.toThrow(); entries[0].approved_review_revision_id = "R2";
  await command.run();
  expect(fetch.mock.calls[0][1]).toEqual(fetch.mock.calls[1][1]);
  expect(JSON.parse(fetch.mock.calls[1][1].body).entries[0].approved_review_revision_id).toBe("R1");
});
it("a definitive command conflict does not endlessly retry", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({detail: "paper_publication_request_key_conflict"}), {status: 409})); vi.stubGlobal("fetch", fetch);
  const command = api.preparePublish("p", "R1");
  await expect(command.run()).rejects.toBeInstanceOf(ApiError);
  await expect(command.run()).rejects.toBeInstanceOf(ApiError);
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("same-revision republish is a new command; old replay stays historical", async () => {
  const fetch = vi.fn().mockImplementation(async () => success()); vi.stubGlobal("fetch", fetch);
  const original = api.preparePublish("p", "R1"); await original.run();
  await api.prepareWithdraw("p", "publication-A", "Исправление").run();
  const republish = api.preparePublish("p", "R1"); await republish.run();
  expect(republish.key).not.toBe(original.key);
  expect(await original.run()).toEqual({id: "publication-A", active: false});
  expect(fetch).toHaveBeenCalledTimes(3);
});
it.each([false, true])("bulk publish uses one official endpoint (automatic=%s)", async automatic => {
  const fetch = vi.fn().mockResolvedValue(success()); vi.stubGlobal("fetch", fetch);
  const command = automatic ? api.prepareBulkPublish("b") : api.prepareSelectedPublish("b", [
    {paper_submission_id: "p1", approved_review_revision_id: "R1"}, {paper_submission_id: "p2", approved_review_revision_id: "R2"},
  ]); await command.run();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(path(fetch.mock.calls[0])).toBe(`/api/assessment-core/scan-batches/b/publications/${automatic ? "bulk" : "selected"}`);
  expect(fetch.mock.calls[0][1].headers["Idempotency-Key"]).toBe(command.key);
});
it("withdrawal targets exact old identity, never substitutes newer publication", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({detail: "paper_publication_not_active"}), {status: 409})); vi.stubGlobal("fetch", fetch);
  const command = api.prepareWithdraw("p", "old/A", "  Исправление  ");
  await expect(command.run()).rejects.toMatchObject({code: "paper_publication_not_active"});
  expect(path(fetch.mock.calls[0])).toBe("/api/assessment-core/paper-submissions/p/publications/old%2FA/withdraw");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({reason: "Исправление"});
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("bulk withdrawal sends exact identities and one common reason", async () => {
  const fetch = vi.fn().mockResolvedValue(success()); vi.stubGlobal("fetch", fetch);
  const entries = [{paper_submission_id: "p1", publication_id: "A"}, {paper_submission_id: "p2", publication_id: "B"}];
  const command = api.prepareSelectedWithdraw("b", entries, "  Ошибка  "); await command.run();
  expect(path(fetch.mock.calls[0])).toBe("/api/assessment-core/scan-batches/b/publications/withdraw/selected");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({entries, reason: "Ошибка"});
  expect(fetch.mock.calls[0][1].headers["Idempotency-Key"]).toBe(command.key);
  expect(fetch).toHaveBeenCalledTimes(1);
});
it.each(["", "   ", "x".repeat(501)])("rejects invalid reason before transport", reason => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  expect(() => api.prepareWithdraw("p", "A", reason)).toThrow(ApiError);
  expect(() => api.prepareSelectedWithdraw("b", [], reason)).toThrow(ApiError);
  expect(fetch).not.toHaveBeenCalled();
});
it("accepts 500 Unicode characters after trimming, matching backend", () => {
  expect(api.withdrawalReason(` ${"😀".repeat(500)} `)).toBe("😀".repeat(500));
});
it("history and progress reads do not write; completion is explicit", async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response("{}")); vi.stubGlobal("fetch", fetch);
  await api.readPublicationHistory("p"); await api.readPublicationProgress("b");
  expect(fetch.mock.calls.every(c => !c[1].method)).toBe(true);
  await api.completePaperBatch("b");
  expect(path(fetch.mock.calls[2])).toBe("/api/assessment-core/scan-batches/b/complete");
  expect(fetch.mock.calls[2][1].method).toBe("POST");
});
it.each(["paper_publication_pages_unavailable", "paper_publication_request_key_conflict", "paper_publication_not_active", "paper_publication_revision_not_approved", "paper_publication_withdrawal_reason_required", "paper_publication_withdrawal_reason_invalid", "paper_batch_publications_incomplete"])("maps %s without exposing server text", code => {
  const message = scanErrorMessage(new ApiError(409, code, "sensitive server content"));
  expect(message).not.toContain("sensitive"); expect(message).not.toContain("Состояние изменилось.");
});
it.each([401, 403, 404])("preserves bounded authorization denial (%s)", status => {
  expect(scanErrorMessage(new ApiError(status, "unexpected_error", "sensitive"))).not.toContain("sensitive");
});
