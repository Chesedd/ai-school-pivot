import type {Methodology} from "./methodology";
export const apiBase=import.meta.env.VITE_API_BASE_URL??"http://localhost:8000";
export type ValidationIssue={field?:string;code?:string;message:string};
export class ApiError extends Error{constructor(public status:number,public code:string,message:string,public details:any=[]){super(message)}}
async function request(path:string,init?:RequestInit){const response=await fetch(`${apiBase}${path}`,init);let data:any={};try{data=await response.json()}catch{}if(!response.ok)throw new ApiError(response.status,data.error?.code??"unexpected_error",data.error?.message??"Ошибка API",data.error?.details??[]);return data}
export type Folder={id:string;subject_id:string;parent_id:string|null;name:string;depth:number;created_at:string;updated_at:string};
export type FolderNode=Folder&{children:FolderNode[]};
export type SubjectRoot={id:string;name:string};
export type CatalogItem={id:string;name:string;subject_id?:string;grade_id?:string;topic_id?:string;subtopic_id?:string};
export type LevelContents={subject:SubjectRoot;folder:Folder|null;breadcrumb:Folder[];folders:Folder[];tasks:{items:any[];total:number;offset:number;limit:number};level_task_total:number;subject_task_total:number};
const enc=encodeURIComponent;
export const getSubjectRoots=(signal?:AbortSignal):Promise<{items:SubjectRoot[]}>=>(request("/api/content-bank/catalog/subjects",{signal}));
export const getCatalog=(name:"grades"|"topics"|"subtopics"|"skills",signal?:AbortSignal):Promise<{items:CatalogItem[]}>=>(request(`/api/content-bank/catalog/${name}`,{signal}));
export const getFolderTree=(subjectId:string,signal?:AbortSignal):Promise<{subject:SubjectRoot;folders:FolderNode[]}>=>(request(`/api/content-bank/subjects/${enc(subjectId)}/folders/tree`,{signal}));
export const getLevelContents=(subjectId:string,folderId:string|null,params:URLSearchParams,signal?:AbortSignal):Promise<LevelContents>=>request(`${folderId?`/api/content-bank/folders/${enc(folderId)}/contents`:`/api/content-bank/subjects/${enc(subjectId)}/contents`}?${params}`,{signal});
export const createFolder=(subjectId:string,body:{name:string;parent_id:string|null}):Promise<Folder>=>request(`/api/content-bank/subjects/${enc(subjectId)}/folders`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
export const renameFolder=(id:string,body:{name:string;expected_updated_at:string}):Promise<Folder>=>request(`/api/content-bank/folders/${enc(id)}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
export const moveFolder=(id:string,body:{parent_id:string|null;expected_updated_at:string}):Promise<Folder>=>request(`/api/content-bank/folders/${enc(id)}/move`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
export const deleteFolder=(id:string,expected:string):Promise<void>=>request(`/api/content-bank/folders/${enc(id)}?expected_updated_at=${enc(expected)}`,{method:"DELETE"});
export const moveTask=(id:string,body:{folder_id:string|null;expected_folder_id:string|null})=>request(`/api/content-bank/tasks/${enc(id)}/location`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
export type ImportRowPayload={row_number:number;subject_id:string;grade_id:string;topic_id:string;subtopic_id:string|null;tags?:string[];initial_version:{title:string|null;statement:string;task_type:string;answer_format:string;difficulty:number;source:string|null;skills:{skill_id:string;weight:string;is_primary:boolean}[]}};
export type ImportPreview={import_token:string;format:"csv"|"xlsx";expires_at:string;can_commit:boolean;summary:{rows_total:number;rows_valid:number;rows_invalid:number};rows:{row_number:number;status:"valid"|"invalid";raw_tag_names?:string[];resolved_tags?:{input:string;tag_id:string;name:string;category_code:string;subject_id:string|null;status:string;replacement?:{id:string;name:string}|null}[];issues:{severity:string;code:string;field:string;message:string;value?:string;duplicate_candidates?:DuplicateCandidate[];duplicate_row_number?:number|null}[]}[]};
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

export type TagCategory={code:string;name:string;sort_order:number};
export type TagSubject={id:string;code?:string;name:string};
export type TagRef={id:string;name:string;category_code:string;subject_id:string|null;status:"active"|"deprecated"};
export type ManagedTag={id:string;category:TagCategory;subject:TagSubject|null;name:string;normalized_name:string;status:"active"|"deprecated";replacement:TagRef|null;created_at:string;created_by:string;updated_at:string;updated_by:string};
export type TagPage={items:ManagedTag[];total:number;offset:number;limit:number};
export type TagSimilarity={normalized_query:string;items:{tag:TagRef;similarity:number;exact_match:boolean}[]};
export type TagUsage={tag_id:string;historical_version_count:number;distinct_task_count:number;latest_version_count:number;status_counts:Record<string,number>;latest_status_counts:Record<string,number>};
export const getTagCategories=(signal?:AbortSignal):Promise<{items:TagCategory[]}>=>(request("/api/content-bank/tag-categories",{signal}));
export const getTags=(params:URLSearchParams,signal?:AbortSignal):Promise<TagPage>=>request(`/api/content-bank/tags?${params}`,{signal});
export const getTag=(id:string,signal?:AbortSignal):Promise<ManagedTag>=>request(`/api/content-bank/tags/${enc(id)}`,{signal});
export const getSimilarTags=(name:string,excludeId?:string,signal?:AbortSignal):Promise<TagSimilarity>=>{const p=new URLSearchParams({name,limit:"8"});if(excludeId)p.set("exclude_tag_id",excludeId);return request(`/api/content-bank/tags/similar?${p}`,{signal})};
export const createTag=(body:{name:string;category_code:string;subject_id:string|null}):Promise<ManagedTag>=>request("/api/content-bank/admin/tags",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
export const patchTag=(id:string,body:{expected_updated_at:string;name?:string;category_code?:string;subject_id?:string|null;replacement_tag_id?:string|null}):Promise<ManagedTag>=>request(`/api/content-bank/admin/tags/${enc(id)}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
export const deprecateTag=(id:string,body:{expected_updated_at:string;replacement_tag_id:string|null}):Promise<ManagedTag>=>request(`/api/content-bank/admin/tags/${enc(id)}/deprecate`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
export const getTagUsage=(id:string,signal?:AbortSignal):Promise<TagUsage>=>request(`/api/content-bank/admin/tags/${enc(id)}/usage`,{signal});
