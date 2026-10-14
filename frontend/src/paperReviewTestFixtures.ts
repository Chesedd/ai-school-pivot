import type {CheckingProgress, EffectivePaper, PaperReview, ReviewFinding} from "./paperReviewApi";
export const effectivePaper = (overrides: Partial<EffectivePaper> = {}): EffectivePaper => ({
  paper_submission_id: "paper1", assignment_participant_id: "s1", display_name: "Иванов Иван", assigned_variant_id: "v1",
  checking_state: "not_started", effective_check_run_id: null, ai_review_available: false,
  manual_review_available: true, review_id: null, review_state: null, latest_approved_revision_id: null, can_retry: false, can_execute: false, ...overrides,
});
export const progressFixture = (overrides: Partial<CheckingProgress> = {}): CheckingProgress => ({
  batch_id: "b", batch_status: "ready_for_checking", paper_count: 1, settled_count: 0, not_started: 1, pending: 0, running: 0, completed: 0,
  completed_with_review_required: 0, failed_retryable: 0, failed_terminal: 0, manual_reviewing: 0, manual_approved: 0, can_reconcile: false,
  papers: [effectivePaper()], ...overrides,
});
export const reviewFinding = (overrides: Partial<ReviewFinding> = {}): ReviewFinding => ({
  finding_id: "f1", assessment_item_id: "i1", source_check_finding_id: "source-f1", scan_page_id: "p2", category: "work", severity: "warning",
  short_explanation: "Проверьте вычисление", detailed_explanation: null,
  region: {kind: "point", coordinate_space: "normalized_upright_v1", x: "0.25", y: "0.5"}, coordinate_space_version: "normalized_upright_v1", ...overrides,
});
export const reviewFixture = (manual = false): PaperReview => ({
  review_id: "review1", paper_submission_id: "paper1", source_kind: manual ? "manual" : "ai", check_run_id: manual ? null : "run1", ai_revision_id: manual ? null : "ai1",
  state: "reviewing", version: 1, latest_approved_revision_id: null, latest_approved_revision: null,
  draft: {schema_version: "paper_check_review_draft.v1", source_kind: manual ? "manual" : "ai", paper_submission_id: "paper1",
    check_run_id: manual ? null : "run1", ai_revision_id: manual ? null : "ai1", summary: manual ? "" : "Итог проверки",
    items: ["i1", "i2"].map(id => ({assessment_item_id: id, source_check_result_id: manual ? null : `result-${id}`, status: manual ? null : "partially_correct", accepted_score: manual ? null : "1", max_score: "2", teacher_comment: null})),
    findings: manual ? [] : [reviewFinding()],
  },
});
