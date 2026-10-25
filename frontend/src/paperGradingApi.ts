import {request} from "./api";

export type GradingPolicy = {
  schema_version: string; original_teacher_instruction: string;
  assessment_items: {assessment_item_id: string; max_score: string; rubric_source: "authored" | "teacher_override";
    rubric: {text: string; reference?: string | null}; partial_credit_policy: string; special_rules: string[]}[];
  total_max_score: string; general_rules: string[];
  grade_scale: {basis: "points" | "percent"; thresholds: {minimum: string; grade_label: string}[]};
  additional_context: string | null; compiler_version: string; prompt_policy_version: string;
};
export type FrozenPolicy = {id: string; assessment_variant_id: string; revision: number; policy: GradingPolicy};
const path = (id: string) => `/api/assessment-core/scan-batches/${encodeURIComponent(id)}/grading-policies`;
export const readGradingPolicies = (id: string, signal?: AbortSignal): Promise<{items: FrozenPolicy[]}> => request(path(id), {signal});
export const freezeGradingPolicy = (id: string, variant: string, policy: GradingPolicy): Promise<FrozenPolicy> =>
  request(`${path(id)}/${encodeURIComponent(variant)}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(policy)});
export function latestPolicies(items: FrozenPolicy[]) {
  const result = new Map<string, FrozenPolicy>();
  for (const item of items) if ((result.get(item.assessment_variant_id)?.revision || 0) < item.revision) result.set(item.assessment_variant_id, item);
  return result;
}
