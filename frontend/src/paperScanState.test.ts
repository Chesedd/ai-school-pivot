import {expect, it} from "vitest";
import {ApiError} from "./api";
import {acceptsUpload, canExtract, intakeReady, needsPolling, scanErrorMessage} from "./paperScanState";
import {artifactFixture, batchFixture, pageFixture} from "./paperScanTestFixtures";
const ready = () => ({batch: batchFixture({status: "extracting"}), artifacts: [artifactFixture({extraction_status: "completed", page_count: 1})], pages: [pageFixture()]});
it("requires extraction state, every completed source and every ready page", () => {
  expect(intakeReady(ready())).toBe(true);
  for (const state of ["draft", "uploading", "matching", "completed", "processing_failed", "cancelled"]) expect(intakeReady({...ready(), batch: batchFixture({status: state})})).toBe(false);
  for (const state of ["pending", "running", "failed_retryable", "failed_terminal"]) expect(intakeReady({...ready(), artifacts: [artifactFixture({extraction_status: state})]})).toBe(false);
  expect(intakeReady({...ready(), pages: []})).toBe(false);
  expect(intakeReady({...ready(), pages: [pageFixture({status: "failed_terminal"})]})).toBe(false);
  expect(intakeReady({...ready(), pages: [pageFixture({content_url: null})]})).toBe(false);
});
it("only polls active extraction and resumes only backend-legal sources", () => {
  expect(needsPolling(ready())).toBe(false); expect(canExtract(ready())).toBe(false);
  for (const state of ["pending", "running", "failed_retryable", "failed_terminal"]) {
    const data = {...ready(), artifacts: [artifactFixture({extraction_status: state})]};
    expect(needsPolling(data)).toBe(["pending", "running"].includes(state)); expect(canExtract(data)).toBe(state !== "failed_terminal");
  }
  expect(needsPolling({...ready(), batch: batchFixture()})).toBe(false);
  expect(acceptsUpload(batchFixture({status: "completed"}))).toBe(false);
});
it.each([401, 403, 404, 409, 422, 500, 503])("bounds error feedback for status %s", status => {
  expect(scanErrorMessage(new ApiError(status, "unknown", "SQL /private/storage asyncpg traceback"))).not.toMatch(/SQL|storage|asyncpg|traceback/);
});
