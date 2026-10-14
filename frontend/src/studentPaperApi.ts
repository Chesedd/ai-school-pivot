import {request} from "./api";
import type {FindingRegion} from "./paperReviewApi";

export type PublishedItem = {assessment_item_id: string; status: "correct" | "partially_correct" | "incorrect" | "not_attempted"; accepted_score: string | number; max_score: string | number; teacher_comment: string | null};
export type PublishedFinding = {finding_id: string; assessment_item_id: string | null; scan_page_id: string; category: string; severity: "info" | "warning" | "error"; short_explanation: string; detailed_explanation: string | null; region: FindingRegion; coordinate_space_version: "normalized_upright_v1"};
export type PublishedResult = {summary: string; items: PublishedItem[]; findings: PublishedFinding[]; grade: {calculated_total_score: string | number; total_max_score: string | number; calculated_percentage: string | number; calculated_grade: string; final_grade: string}};
export type StudentPaperResult = {assignment_id: string; assignment_title: string; id: string; paper_submission_id: string; publication_number: number; published_at: string; active: true; result: PublishedResult; pages: {scan_page_id: string; page_order: number; width: number; height: number; coordinate_space_version: "normalized_upright_v1"; content_url: string}[]};
const root = "/api/assessment-core/student";
const e = encodeURIComponent;
export const getAssignmentPaperResult = (id: string, signal?: AbortSignal): Promise<StudentPaperResult> => request(`${root}/assignments/${e(id)}/paper-result`, {signal, cache: "no-store"});
export const getStudentPaperPage = (paperId: string, pageId: string, signal?: AbortSignal): Promise<Blob> => request(`${root}/paper-submissions/${e(paperId)}/pages/${e(pageId)}`, {signal, cache: "no-store"}, "blob");
