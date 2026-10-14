import {ApiError, request} from "./api";
import type {CheckingProgress} from "./paperReviewApi";

// Teacher contracts in paper_result_publication_routes.py. No student projection.
export type Publication = {
  id: string; paper_submission_id: string; publication_number: number;
  approved_review_revision_id: string; published_at: string; active: boolean;
  outcome?: "published" | "already_current";
};
export type PublicationHistory = Publication & {
  is_active: boolean; is_withdrawn: boolean; withdrawn_at: string | null;
  withdrawn_by_user_id: string | null; withdrawal_reason: string | null;
};
export type PublicationEntry = {paper_submission_id: string; approved_review_revision_id: string};
export type WithdrawalEntry = {paper_submission_id: string; publication_id: string};
export type Withdrawal = {id: string; publication_id: string; withdrawn_by_user_id: string; reason: string; created_at: string};
export type PublicationProgress = CheckingProgress & {
  active_publication_count: number; can_mark_checking_completed: boolean;
  can_finalize: boolean; blocking_reasons: string[];
};
const root = "/api/assessment-core";
const paper = (id: string) => `${root}/paper-submissions/${encodeURIComponent(id)}/publications`;
const batch = (id: string) => `${root}/scan-batches/${encodeURIComponent(id)}`;
export const readPublicationHistory = (id: string, signal?: AbortSignal): Promise<{items: PublicationHistory[]}> =>
  request(paper(id), {signal});
export const readPublicationProgress = (id: string, signal?: AbortSignal): Promise<PublicationProgress> =>
  request(`${batch(id)}/checking`, {signal});
export const completePaperBatch = (id: string): Promise<PublicationProgress> =>
  request(`${batch(id)}/complete`, {method: "POST"});

export type PublicationCommand<T> = {readonly key: string; run: () => Promise<T>};
// Create once at the user action boundary, retain through uncertain transport
// failures, release after a definitive response. New intent means a new object.
// Payload serialization happens now so a retry cannot acquire a different body.
function command<T>(path: string, payload?: unknown): PublicationCommand<T> {
  const key = `paper:${crypto.randomUUID()}`;
  const body = payload === undefined ? undefined : JSON.stringify(payload);
  let inFlight: Promise<T> | undefined;
  let definitive: Promise<T> | undefined;
  return Object.freeze({key, run: () => {
    if (definitive) return definitive;
    if (inFlight) return inFlight;
    const attempt = request<T>(path, {method: "POST", headers: {
      "Idempotency-Key": key, ...(body === undefined ? {} : {"Content-Type": "application/json"}),
    }, body});
    inFlight = attempt;
    void attempt.then(() => {definitive = attempt; inFlight = undefined;}, error => {
      // 5xx/network outcomes may have committed: retry only this frozen command.
      if (error instanceof ApiError && error.status < 500) definitive = attempt;
      inFlight = undefined;
    });
    return attempt;
  }});
}
export const preparePublish = (id: string, approvedRevisionId: string) =>
  command<Publication>(paper(id), {approved_review_revision_id: approvedRevisionId});
export const prepareSelectedPublish = (id: string, entries: PublicationEntry[]) =>
  command<{items: Publication[]}>(`${batch(id)}/publications/selected`, {entries});
export const prepareBulkPublish = (id: string) =>
  command<{items: Publication[]}>(`${batch(id)}/publications/bulk`);
export function withdrawalReason(value: string): string {
  const reason = value.trim();
  if (!reason) throw new ApiError(422, "paper_publication_withdrawal_reason_required", "");
  if ([...reason].length > 500) throw new ApiError(422, "paper_publication_withdrawal_reason_invalid", "");
  return reason;
}
export const prepareWithdraw = (id: string, publicationId: string, reason: string) =>
  command<Withdrawal>(`${paper(id)}/${encodeURIComponent(publicationId)}/withdraw`, {reason: withdrawalReason(reason)});
export const prepareSelectedWithdraw = (id: string, entries: WithdrawalEntry[], reason: string) =>
  command<{items: Withdrawal[]}>(`${batch(id)}/publications/withdraw/selected`, {entries, reason: withdrawalReason(reason)});
