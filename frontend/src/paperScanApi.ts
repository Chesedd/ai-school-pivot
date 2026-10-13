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

// Teacher projections from ScanMatchingService.read / ScanGroupingRepository.read.
export type MatchingStudent = {assignment_participant_id: string; display_name: string};
export type MatchingProposal = {
  page_id: string; source_order: number; content_url: string; disposition: string;
  proposed_student: MatchingStudent | null; candidate_students: MatchingStudent[];
  confidence: number | string; evidence_code: string; evidence_summary: string;
  proposed_page_order: number | null; warning: boolean;
};
export type ScanMatching = {
  batch_id: string; matching_revision: number; status: string;
  pages_summary: {total: number; matched: number; ambiguous: number; unmatched: number};
  warnings: number; proposals: MatchingProposal[];
};
export type GroupingPage = {
  page_id: string; source_order: number; page_order: number | null; content_url: string;
  ai_proposal: {disposition: string; confidence: number | string; evidence_code: string; evidence_summary: string};
};
export type PaperGroup = MatchingStudent & {
  student_id: string; assigned_variant_id: string | null; pages: GroupingPage[];
};
export type ScanGrouping = {
  batch_id: string; batch_status: string; matching_revision: number; grouping_revision: number;
  grouping_status: string; row_version: number; groups: PaperGroup[];
  unresolved_pages: (GroupingPage & {candidate_students: (MatchingStudent & {student_id: string; rank: number})[]; reason_code: string})[];
  paper_submissions: {id: string; assignment_participant_id: string; assigned_variant_id: string; status: string}[];
};
export const readScanMatching = (id: string, signal?: AbortSignal): Promise<ScanMatching> => request(`${batchPath(id)}/matching`, {signal});
export const startScanMatching = (id: string): Promise<ScanMatching> => request(`${batchPath(id)}/match`, {method: "POST"});
export const readScanGrouping = (id: string, signal?: AbortSignal): Promise<ScanGrouping> => request(`${batchPath(id)}/grouping`, {signal});
export const initializeScanGrouping = (id: string): Promise<ScanGrouping> => request(`${batchPath(id)}/grouping`, {method: "POST"});
const groupingCAS = (g: ScanGrouping) => ({expected_revision: g.grouping_revision, expected_row_version: g.row_version});
const groupingBody = (method: string, body: object): RequestInit => ({method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
export const assignScanPage = (id: string, pageId: string, participantId: string | null, g: ScanGrouping): Promise<ScanGrouping> =>
  request(`${batchPath(id)}/grouping/pages/${encodeURIComponent(pageId)}`, groupingBody("PATCH", {...groupingCAS(g), assignment_participant_id: participantId}));
export const reorderScanPages = (id: string, participantId: string, pageIds: string[], g: ScanGrouping): Promise<ScanGrouping> =>
  request(`${batchPath(id)}/grouping/groups/${encodeURIComponent(participantId)}/page-order`, groupingBody("PUT", {...groupingCAS(g), page_ids: pageIds}));
export const confirmScanGrouping = (id: string, g: ScanGrouping): Promise<ScanGrouping> =>
  request(`${batchPath(id)}/grouping/confirm`, groupingBody("POST", groupingCAS(g)));
export const readScanPageContent = (id: string, signal?: AbortSignal): Promise<Blob> =>
  request(`${root}/scan-pages/${encodeURIComponent(id)}/content`, {signal}, "blob");
