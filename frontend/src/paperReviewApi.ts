import {request} from "./api";

// Current scan_intake_routes.py and paper_checking_review.py contracts.
export type EffectivePaper = {
  paper_submission_id: string; assignment_participant_id: string; display_name: string;
  assigned_variant_id: string; checking_state: string; effective_check_run_id: string | null;
  ai_review_available: boolean; manual_review_available: boolean; review_id: string | null;
  review_state: string | null; latest_approved_revision_id: string | null;
  can_retry: boolean; can_execute: boolean;
};
export type CheckingProgress = {
  batch_id: string; batch_status: string; paper_count: number; settled_count: number;
  not_started: number; pending: number; running: number; completed: number;
  completed_with_review_required: number; failed_retryable: number; failed_terminal: number;
  manual_reviewing: number; manual_approved: number; can_reconcile: boolean;
  papers: EffectivePaper[];
};
export type ItemStatus = "correct" | "partially_correct" | "incorrect" | "not_attempted" | "unreadable" | "insufficient_evidence";
export type ReviewItem = {
  assessment_item_id: string; source_check_result_id: string | null; status: ItemStatus | null;
  accepted_score: string | number | null; max_score: string | number; teacher_comment: string | null;
};
export type FindingRegion = {kind: "point"; coordinate_space: "normalized_upright_v1"; x: number | string; y: number | string} |
  {kind: "box"; coordinate_space: "normalized_upright_v1"; x: number | string; y: number | string; width: number | string; height: number | string};
export type ReviewFinding = {
  finding_id: string; assessment_item_id: string | null; source_check_finding_id: string | null;
  scan_page_id: string; category: string; severity: "info" | "warning" | "error";
  short_explanation: string; detailed_explanation: string | null; region: FindingRegion;
  coordinate_space_version: "normalized_upright_v1";
};
export type ReviewDraft = {
  schema_version: "paper_check_review_draft.v1"; source_kind: "ai" | "manual";
  paper_submission_id: string; check_run_id: string | null; ai_revision_id: string | null;
  summary: string; items: ReviewItem[]; findings: ReviewFinding[];
};
export type ReviewGrade = {
  calculated_total_score: string | number; total_max_score: string | number;
  calculated_percentage: string | number; calculated_grade: string; final_grade: string;
  grade_override_reason: string | null;
};
export type ReviewRevision = {id: string; revision_number: number; revision_kind: string; snapshot: ReviewDraft; grade_result: ReviewGrade | null};
export type PaperReview = {
  review_id: string; paper_submission_id: string; source_kind: "ai" | "manual";
  check_run_id: string | null; ai_revision_id: string | null; state: "reviewing" | "approved";
  version: number; draft: ReviewDraft; latest_approved_revision_id: string | null;
  latest_approved_revision: ReviewRevision | null;
};
export type AISuggestion = {assessment_item_id: string; status: ItemStatus; suggested_score: string | number | null; max_score: string | number};
export type AIResult = {items: AISuggestion[]; summary_draft: string | null};
export type GradeOverride = {override_grade: string; reason: string};
const root = "/api/assessment-core";
const enc = encodeURIComponent;
const checking = (id: string) => `${root}/scan-batches/${enc(id)}/checking`;
const paperPath = (id: string) => `${root}/paper-submissions/${enc(id)}`;
const reviewPath = (p: EffectivePaper) => p.effective_check_run_id
  ? `${paperPath(p.paper_submission_id)}/check-runs/${enc(p.effective_check_run_id)}/review`
  : `${paperPath(p.paper_submission_id)}/manual-review`;
const json = (method: string, value: unknown): RequestInit => ({method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(value)});
export const readChecking = (id: string, signal?: AbortSignal): Promise<CheckingProgress> => request(checking(id), {signal});
export const startChecking = (id: string): Promise<CheckingProgress> => request(`${checking(id)}/start`, {method: "POST"});
export const reconcileChecking = (id: string): Promise<CheckingProgress> => request(`${checking(id)}/reconcile`, {method: "POST"});
export const executeChecking = (id: string): Promise<{outcomes: {check_run_id: string; status?: string; code?: string}[]; progress: CheckingProgress}> =>
  request(`${checking(id)}/execute`, {method: "POST"});
export const readReview = (p: EffectivePaper, signal?: AbortSignal): Promise<PaperReview> => request(reviewPath(p), {signal});
export const openReview = (p: EffectivePaper): Promise<PaperReview> => request(reviewPath(p), {method: "POST"});
export const saveReview = (p: EffectivePaper, version: number, draft: ReviewDraft): Promise<PaperReview> => request(reviewPath(p), json("PUT", {expected_version: version, draft}));
export const approveReview = (p: EffectivePaper, version: number, grade_override: GradeOverride | null): Promise<PaperReview> =>
  request(`${reviewPath(p)}/approve`, json("POST", {expected_version: version, grade_override}));
export const readAISuggestions = (p: EffectivePaper, signal?: AbortSignal): Promise<AIResult> =>
  request(`${paperPath(p.paper_submission_id)}/check-runs/${enc(p.effective_check_run_id!)}/ai-result`, {signal});
export const retryPaper = (p: EffectivePaper, request_key: string): Promise<unknown> => request(`${paperPath(p.paper_submission_id)}/check-runs`, json("POST", {
  request_key, supersedes_run_id: p.effective_check_run_id,
  routing_version: "checking_routing_contract_v1", checker_set_version: "paper_vision_v1",
  threshold_policy_version: "paper_grading_policy.v1", prompt_model_policy_version: "1.0.0",
}));
