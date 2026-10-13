import {request} from "./api";

// Handwritten HTTP contracts, matching scan_intake_routes.py / scan_intake_schemas.py.
export type ScanBatch = {
  id: string; assignment_id: string; status: string; instruction: string;
  created_at: string; updated_at: string;
};
export type ScanArtifact = {
  id: string; upload_position: number; original_filename: string; mime_type: string;
  size_bytes: number; extraction_status: string; page_count: number | null;
  failure_code: string | null; created_at: string; updated_at: string;
};
export type ScanPage = {
  id: string; source_artifact_id: string; source_page_index: number;
  width_px: number | null; height_px: number | null; status: string;
  failure_code: string | null; content_fingerprint: string | null; content_url: string | null;
};
export type ExtractionSummary = {
  batch_id: string; status: string;
  artifacts: {completed: number; failed_retryable: number; failed_terminal: number};
  pages: Record<string, number>;
};
const root = "/api/assessment-core";
const batchPath = (id: string) => `${root}/scan-batches/${encodeURIComponent(id)}`;
export const listScanBatches = (assignmentId: string, signal?: AbortSignal): Promise<{items: ScanBatch[]}> =>
  request(`${root}/assignments/${encodeURIComponent(assignmentId)}/scan-batches`, {signal});
export const createScanBatch = (assignmentId: string, instruction: string, key: string): Promise<ScanBatch> =>
  request(`${root}/assignments/${encodeURIComponent(assignmentId)}/scan-batches`, {
    method: "POST", headers: {"Content-Type": "application/json", "Idempotency-Key": key},
    body: JSON.stringify({instruction}),
  });
export const getScanBatch = (id: string, signal?: AbortSignal): Promise<ScanBatch> => request(batchPath(id), {signal});
export const listScanArtifacts = (id: string, signal?: AbortSignal): Promise<{items: ScanArtifact[]}> => request(`${batchPath(id)}/artifacts`, {signal});
export const listScanPages = (id: string, signal?: AbortSignal): Promise<{items: ScanPage[]}> => request(`${batchPath(id)}/pages`, {signal});
export function uploadScanArtifact(id: string, file: File, position: number): Promise<ScanArtifact> {
  const body = new FormData();
  body.append("file", file);
  body.append("upload_position", String(position));
  return request(`${batchPath(id)}/artifacts`, {method: "POST", body});
}
export const extractScanBatch = (id: string): Promise<ExtractionSummary> => request(`${batchPath(id)}/extract`, {method: "POST"});
export const downloadScanArtifact = (batchId: string, artifactId: string): Promise<Blob> =>
  request(`${batchPath(batchId)}/artifacts/${encodeURIComponent(artifactId)}/content`, undefined, "blob");
