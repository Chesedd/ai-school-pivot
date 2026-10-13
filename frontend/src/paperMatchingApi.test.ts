import {afterEach, expect, it, vi} from "vitest";
import * as api from "./paperScanApi";
import {ApiError} from "./api";
import {matchingErrorMessage} from "./paperScanState";
import {groupingFixture} from "./paperMatchingTestFixtures";
afterEach(() => vi.unstubAllGlobals());
it("uses exact matching/grouping routes, HTTP methods, CAS and no frontend variant payload", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response("{}", {headers: {"Content-Type": "application/json"}})); vi.stubGlobal("fetch", fetcher);
  const g = groupingFixture({grouping_revision: 4, row_version: 19});
  await api.startScanMatching("b/a"); await api.readScanMatching("b/a"); await api.initializeScanGrouping("b/a"); await api.readScanGrouping("b/a");
  await api.assignScanPage("b/a", "p/a", "s1", g); await api.assignScanPage("b/a", "p/a", null, g);
  await api.reorderScanPages("b/a", "s/a", ["p2", "p1"], g); await api.confirmScanGrouping("b/a", g);
  expect(fetcher.mock.calls.map(c => c[0].split("/scan-batches/")[1])).toEqual(["b%2Fa/match", "b%2Fa/matching", "b%2Fa/grouping", "b%2Fa/grouping", "b%2Fa/grouping/pages/p%2Fa", "b%2Fa/grouping/pages/p%2Fa", "b%2Fa/grouping/groups/s%2Fa/page-order", "b%2Fa/grouping/confirm"]);
  expect(fetcher.mock.calls.map(c => c[1].method || "GET")).toEqual(["POST", "GET", "POST", "GET", "PATCH", "PATCH", "PUT", "POST"]);
  expect(JSON.parse(fetcher.mock.calls[4][1].body)).toEqual({expected_revision: 4, expected_row_version: 19, assignment_participant_id: "s1"});
  expect(JSON.parse(fetcher.mock.calls[5][1].body).assignment_participant_id).toBeNull();
  expect(JSON.parse(fetcher.mock.calls[6][1].body)).toEqual({expected_revision: 4, expected_row_version: 19, page_ids: ["p2", "p1"]});
  expect(JSON.parse(fetcher.mock.calls[7][1].body)).toEqual({expected_revision: 4, expected_row_version: 19});
  fetcher.mock.calls.forEach(c => expect(c[1].credentials).toBe("include"));
});
it("fetches authorized normalized content bytes with abort signal", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response("png", {headers: {"Content-Type": "image/png"}})); vi.stubGlobal("fetch", fetcher); const controller = new AbortController();
  expect(await (await api.readScanPageContent("p/a", controller.signal)).text()).toBe("png");
  expect(fetcher.mock.calls[0][0]).toContain("/api/assessment-core/scan-pages/p%2Fa/content"); expect(fetcher.mock.calls[0][1]).toMatchObject({credentials: "include", signal: controller.signal});
});
it.each(["grouping_revision_conflict", "grouping_based_on_stale_matching", "grouping_immutable", "grouping_participant_not_in_assignment", "grouping_variant_required", "unmatched_pages_block_confirmation", "grouping_page_order_mismatch", "matching_already_running"])("maps exact server code %s without rendering details", code => {
  expect(matchingErrorMessage(new ApiError(409, code, "SQL /private provider"))).not.toContain("SQL");
  expect(matchingErrorMessage(new ApiError(409, code, "private"))).not.toBe(matchingErrorMessage(new ApiError(409, "unknown", "private")));
});
it("bounds unknown 5xx and arbitrary network errors", () => {
  expect(matchingErrorMessage(new ApiError(500, "provider_error", "raw request"))).toBe("Ошибка сервера. Обновите данные и повторите действие.");
  expect(matchingErrorMessage(new Error("private path"))).not.toContain("private");
  expect(matchingErrorMessage(new ApiError(422, "unexpected_internal_error", "raw"))).toBe("Не удалось завершить сопоставление. Данные обновлены.");
  expect(matchingErrorMessage(new ApiError(422, "unknown", "raw"))).toBe("Не удалось выполнить действие. Обновите данные и проверьте распределение.");
});
