import type {Methodology} from "./methodology";
export const apiBase=import.meta.env.VITE_API_BASE_URL??"http://localhost:8000";
export type ValidationIssue={field?:string;code?:string;message:string};
export class ApiError extends Error{constructor(public status:number,public code:string,message:string,public details:any=[]){super(message)}}
async function request(path:string,init?:RequestInit){const response=await fetch(`${apiBase}${path}`,init);let data:any={};try{data=await response.json()}catch{}if(!response.ok)throw new ApiError(response.status,data.error?.code??"unexpected_error",data.error?.message??"Ошибка API",data.error?.details??[]);return data}
export type ImportRowPayload={row_number:number;subject_id:string;grade_id:string;topic_id:string;subtopic_id:string|null;initial_version:{title:string|null;statement:string;task_type:string;answer_format:string;difficulty:number;source:string|null;skills:{skill_id:string;weight:string;is_primary:boolean}[]}};
export type ImportPreview={import_token:string;format:"csv"|"xlsx";expires_at:string;can_commit:boolean;summary:{rows_total:number;rows_valid:number;rows_invalid:number};rows:{row_number:number;status:"valid"|"invalid";issues:{severity:string;code:string;field:string;message:string;duplicate_candidates?:DuplicateCandidate[];duplicate_row_number?:number|null}[]}[]};
export type ImportCommit={imported_count:number;items:{row_number:number;task_id:string;task_version_id:string;version_no:number;status:string}[]};
export const previewTaskImport=(payload:{format:"csv"|"xlsx";rows:ImportRowPayload[]},signal?:AbortSignal):Promise<ImportPreview>=>request("/api/content-bank/imports/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal});
export const commitTaskImport=(payload:{import_token:string;row_numbers:number[]},signal?:AbortSignal):Promise<ImportCommit>=>request("/api/content-bank/imports/commit",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal});
export type AuditAction="task_created"|"methodology_updated"|"submitted_for_review"|"returned_to_draft"|"version_approved"|"version_created"|"task_archived";
export type AuditEvent={id:string;task_id:string;task_version_id:string|null;version_no:number|null;action:AuditAction;actor_id:string;reason:string|null;details:Record<string,unknown>;occurred_at:string};
export type AuditPage={items:AuditEvent[];total:number;offset:number;limit:number};
export type AuditQuery={offset?:number;limit?:number;action?:AuditAction|null;signal?:AbortSignal};
export function getTaskAudit(taskId:string,{offset=0,limit=50,action,signal}:AuditQuery={}):Promise<AuditPage>{const params=new URLSearchParams({offset:String(offset),limit:String(limit)});if(action)params.set("action",action);return request(`/api/content-bank/tasks/${encodeURIComponent(taskId)}/audit?${params.toString()}`,{signal})}
const json=(body:unknown):RequestInit=>({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
export const getTaskCard=(id:string)=>request(`/api/content-bank/tasks/${id}`);
export const putMethodology=(versionId:string,payload:unknown):Promise<Methodology>=>request(`/api/content-bank/task-versions/${versionId}/methodology`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
export const submitReview=(taskId:string,versionNo:number)=>request(`/api/content-bank/tasks/${taskId}/versions/${versionNo}/submit-review`,json({}));
export const returnToDraft=(taskId:string,versionNo:number,reason:string)=>request(`/api/content-bank/tasks/${taskId}/versions/${versionNo}/return-to-draft`,json({reason}));
export const approveVersion=(taskId:string,versionNo:number)=>request(`/api/content-bank/tasks/${taskId}/versions/${versionNo}/approve`,json({}));
export async function createTaskVersion(taskId:string,sourceVersionNo:number){const response=await fetch(`${apiBase}/api/content-bank/tasks/${taskId}/versions`,json({source_version_no:sourceVersionNo}));let data:any={};try{data=await response.json()}catch{}if(!response.ok)throw new ApiError(response.status,data.error?.code??"unexpected_error",data.error?.message??"Ошибка API",data.error?.details??[]);return {data,location:response.headers.get("Location")}}
export const archiveTask=(taskId:string,reason?:string)=>request(`/api/content-bank/tasks/${taskId}/archive`,reason?json({reason}):{method:"POST"});

export type DuplicateReason="exact_statement"|"high_statement_similarity"|"same_primary_skill"|"same_final_answer";
export type DuplicateCandidate={task_id:string;task_version_id:string;version_no:number;title:string|null;status:"draft"|"review"|"approved";statement:string;statement_similarity:number;same_primary_skill:boolean;same_final_answer:boolean;reasons:DuplicateReason[]};
export type DuplicateCheckResponse={has_likely_duplicates:boolean;items:DuplicateCandidate[]};
export function checkTaskDuplicates(payload:{statement:string;primary_skill_id:string;final_answer:string|null;exclude_task_id:string|null;limit:number},signal?:AbortSignal):Promise<DuplicateCheckResponse>{return request("/api/content-bank/task-versions/check-duplicates",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal})}
