import type {CSSProperties} from "react";
import type {CheckingProgress, EffectivePaper, FindingRegion, ReviewDraft, ReviewItem} from "./paperReviewApi";

export const itemStatusLabels = {
  correct: "Верно", partially_correct: "Частично верно", incorrect: "Неверно",
  not_attempted: "Не выполнено", unreadable: "Невозможно прочитать", insufficient_evidence: "Недостаточно данных",
};
const paperLabels: Record<string, string> = {
  not_started: "Не проверялась", pending: "Ожидает очереди", running: "Проверяется",
  completed: "Проверена", completed_with_review_required: "Нужно проверить",
  failed_retryable: "Можно повторить", failed_terminal: "Проверка не удалась",
  manual_reviewing: "Ручная проверка · Черновик", manual_approved: "Ручная проверка · Подтверждено",
};
export function paperLabel(p: EffectivePaper) {
  if (p.checking_state.startsWith("manual_")) return paperLabels[p.checking_state] || "Ручная проверка";
  if (p.review_state === "approved") return "Подтверждено";
  if (p.review_state === "reviewing") return "Черновик";
  return paperLabels[p.checking_state] || "Состояние уточняется";
}
export const transientChecking = (p: CheckingProgress) => p.pending > 0 || p.running > 0;
// Terminal failures may support a new backend lineage, but PR3 exposes retry
// only for retryable failures. Capacity wait is never a retry action.
export const canRetryPaper = (p: EffectivePaper) => p.can_retry && p.checking_state === "failed_retryable" && !!p.effective_check_run_id;
export function itemApprovalError(item: ReviewItem): string {
  const score = item.accepted_score === null || item.accepted_score === "" ? NaN : Number(item.accepted_score);
  const max = Number(item.max_score);
  if (!item.status || ["unreadable", "insufficient_evidence"].includes(item.status) || !Number.isFinite(score)) return "Оцените все задания перед подтверждением.";
  if (score < 0 || score > max) return "Баллы должны быть от 0 до максимума задания.";
  const valid = item.status === "correct" ? score === max : item.status === "partially_correct" ? score > 0 && score < max : score === 0;
  return valid ? "" : "Решение и баллы должны соответствовать друг другу.";
}
export function approvalError(d: ReviewDraft) {
  if (!d.summary.trim()) return "Добавьте итоговый комментарий перед подтверждением.";
  if (d.findings.some(f => !f.assessment_item_id)) return "Укажите задание для замечаний без связи или удалите их.";
  return d.items.map(itemApprovalError).find(Boolean) || "";
}
// Percentages are relative to the upright image surface, never the viewport.
// Unknown versions and invalid extents fail closed instead of guessed geometry.
export function regionStyle(r: FindingRegion): CSSProperties | undefined {
  if (r.coordinate_space !== "normalized_upright_v1") return;
  const x = Number(r.x), y = Number(r.y);
  if (![x, y].every(n => Number.isFinite(n) && n >= 0 && n <= 1)) return;
  if (r.kind === "point") return {left: `${x * 100}%`, top: `${y * 100}%`};
  const width = Number(r.width), height = Number(r.height);
  if (![width, height].every(n => Number.isFinite(n) && n >= 0) || x + width > 1 || y + height > 1) return;
  return {left: `${x * 100}%`, top: `${y * 100}%`, width: `${width * 100}%`, height: `${height * 100}%`};
}
