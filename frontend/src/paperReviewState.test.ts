import {expect, it} from "vitest";
import {ApiError} from "./api";
import {scanErrorMessage} from "./paperScanState";
import {approvalError, canRetryPaper, itemApprovalError, paperLabel, regionStyle, transientChecking} from "./paperReviewState";
import {effectivePaper, progressFixture, reviewFixture} from "./paperReviewTestFixtures";
it.each([
  ["not_started", "Не проверялась"], ["pending", "Ожидает очереди"], ["running", "Проверяется"],
  ["completed_with_review_required", "Нужно проверить"], ["failed_retryable", "Можно повторить"], ["failed_terminal", "Проверка не удалась"],
  ["manual_reviewing", "Ручная проверка · Черновик"], ["manual_approved", "Ручная проверка · Подтверждено"],
])("maps effective %s to teacher state", (checking_state, label) => expect(paperLabel(effectivePaper({checking_state}))).toBe(label));
it("maps persisted AI draft and approval", () => {
  expect(paperLabel(effectivePaper({review_state: "reviewing"}))).toBe("Черновик");
  expect(paperLabel(effectivePaper({review_state: "approved"}))).toBe("Подтверждено");
});
it.each(["not_started", "pending", "running", "completed", "failed_terminal", "manual_reviewing", "manual_approved"])("does not offer a retry for %s", checking_state => expect(canRetryPaper(effectivePaper({checking_state, can_retry: true, effective_check_run_id: "r"}))).toBe(false));
it("requires server capability and effective identity for retry", () => {
  expect(canRetryPaper(effectivePaper({checking_state: "failed_retryable", can_retry: true, effective_check_run_id: "r"}))).toBe(true);
  expect(canRetryPaper(effectivePaper({checking_state: "failed_retryable", can_retry: false, effective_check_run_id: "r"}))).toBe(false);
});
it("polls only queued or running checks", () => {
  expect(transientChecking(progressFixture())).toBe(false);
  expect(transientChecking(progressFixture({pending: 1}))).toBe(true);
  expect(transientChecking(progressFixture({running: 1}))).toBe(true);
});
it("normalizes point and box against image, preserving fractional decimal coordinates", () => {
  expect(regionStyle({kind: "point", coordinate_space: "normalized_upright_v1", x: "0.25", y: "0.5"})).toEqual({left: "25%", top: "50%"});
  expect(regionStyle({kind: "box", coordinate_space: "normalized_upright_v1", x: .2, y: .3, width: .4, height: .5})).toEqual({left: "20%", top: "30%", width: "40%", height: "50%"});
});
it.each([NaN, -1, 1.1])("rejects invalid coordinate %s", x => expect(regionStyle({kind: "point", coordinate_space: "normalized_upright_v1", x, y: 0})).toBeUndefined());
it("rejects box outside upright page", () => expect(regionStyle({kind: "box", coordinate_space: "normalized_upright_v1", x: .8, y: .2, width: .4, height: .2})).toBeUndefined());
it.each([null, "unreadable", "insufficient_evidence"] as const)("blocks unresolved approval %s", status => expect(itemApprovalError({...reviewFixture().draft.items[0], status})).not.toBe(""));
it.each([["correct", 1], ["incorrect", 1], ["partially_correct", 2], ["not_attempted", 1]] as const)("blocks status-score mismatch %s/%s", (status, accepted_score) => expect(itemApprovalError({...reviewFixture().draft.items[0], status, accepted_score})).not.toBe(""));
it("requires nonempty summary and canonical linked findings", () => {
  const d = reviewFixture().draft; expect(approvalError(d)).toBe("");
  expect(approvalError({...d, summary: ""})).not.toBe("");
  expect(approvalError({...d, findings: [{...d.findings[0], assessment_item_id: null}]})).not.toBe("");
});
it("capacity and unknown failure messages never leak private details", () => {
  expect(scanErrorMessage(new ApiError(409, "paper_check_capacity_unavailable", "provider secret"))).toContain("ожидают");
  expect(scanErrorMessage(new ApiError(500, "unknown", "SQL asyncpg /private"))).not.toMatch(/SQL|asyncpg|private/);
});
