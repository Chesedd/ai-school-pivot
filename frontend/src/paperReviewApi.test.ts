import {afterEach, expect, it, vi} from "vitest";
import * as api from "./paperReviewApi";
import {effectivePaper, reviewFixture} from "./paperReviewTestFixtures";
afterEach(() => vi.unstubAllGlobals());
it("uses high-level batch start, execute, reconcile and bounded projection", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("{}", {status: 200})); vi.stubGlobal("fetch", fetch);
  await api.readChecking("b"); await api.startChecking("b"); await api.executeChecking("b"); await api.reconcileChecking("b");
  expect(fetch.mock.calls.map(c => [String(c[0]).replace(/^https?:\/\/[^/]+/, ""), c[1].method || "GET"])).toEqual([
    ["/api/assessment-core/scan-batches/b/checking", "GET"], ["/api/assessment-core/scan-batches/b/checking/start", "POST"],
    ["/api/assessment-core/scan-batches/b/checking/execute", "POST"], ["/api/assessment-core/scan-batches/b/checking/reconcile", "POST"],
  ]);
});
it.each([true, false])("uses real review source paths and CAS (AI=%s), never publication", async ai => {
  const fetch = vi.fn().mockResolvedValue(new Response("{}")); vi.stubGlobal("fetch", fetch);
  const p = effectivePaper({effective_check_run_id: ai ? "run1" : null}); const d = reviewFixture(!ai).draft;
  await api.openReview(p); await api.readReview(p); await api.saveReview(p, 7, d); await api.approveReview(p, 8, {override_grade: "4", reason: "Обоснование"});
  const suffix = ai ? "/check-runs/run1/review" : "/manual-review";
  expect(fetch.mock.calls.every(c => String(c[0]).includes(`/paper-submissions/paper1${suffix}`))).toBe(true);
  expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({expected_version: 7, draft: d});
  expect(JSON.parse(fetch.mock.calls[3][1].body)).toEqual({expected_version: 8, grade_override: {override_grade: "4", reason: "Обоснование"}});
  expect(fetch.mock.calls.some(c => /publish|complete|withdraw/.test(String(c[0])))).toBe(false);
});
it("retry creates a superseding run with a retained request key", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("{}")); vi.stubGlobal("fetch", fetch);
  await api.retryPaper(effectivePaper({effective_check_run_id: "old"}), "command-key");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({request_key: "command-key", supersedes_run_id: "old", routing_version: "checking_routing_contract_v1"});
});
