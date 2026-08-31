import type {Methodology} from "./methodology";
export const apiBase=import.meta.env.VITE_API_BASE_URL??"http://localhost:8000";
export type ValidationIssue={field?:string;code?:string;message:string};
export class ApiError extends Error{constructor(public status:number,public code:string,message:string,public details:any=[]){super(message)}}
type UnauthorizedHandler=()=>void;
let unauthorizedHandler:UnauthorizedHandler|undefined;
export const setUnauthorizedHandler=(handler?:UnauthorizedHandler)=>{unauthorizedHandler=handler};
export async function request<T=any>(path:string,init?:RequestInit):Promise<T>{
 const response=await fetch(`${apiBase}${path}`,{...init,credentials:"include"});let data:any={};
 if(response.status!==204)try{data=await response.clone().json()}catch{}
 if(!response.ok){const detail=typeof data?.detail==="string"?data.detail:undefined;const error=new ApiError(response.status,data.error?.code??detail??"unexpected_error",data.error?.message??"Ошибка API",data.error?.details??[]);if(response.status===401&&error.code==="authentication_required")unauthorizedHandler?.();throw error}return data as T
}
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
export type AuditAction="tag_added_to_version"|"tag_removed_from_version"|"task_created"|"methodology_updated"|"submitted_for_review"|"returned_to_draft"|"version_approved"|"version_created"|"task_archived";
export type AuditEvent={id:string;task_id:string;task_version_id:string|null;version_no:number|null;action:AuditAction;actor_id:string;reason:string|null;details:Record<string,unknown>;occurred_at:string};
export type AuditPage={items:AuditEvent[];total:number;offset:number;limit:number};
export type AuditQuery={offset?:number;limit?:number;action?:AuditAction|null;signal?:AbortSignal};
export function getTaskAudit(taskId:string,{offset=0,limit=50,action,signal}:AuditQuery={}):Promise<AuditPage>{const params=new URLSearchParams({offset:String(offset),limit:String(limit)});if(action)params.set("action",action);return request(`/api/content-bank/tasks/${encodeURIComponent(taskId)}/audit?${params.toString()}`,{signal})}
const json=(body:unknown):RequestInit=>({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
export const getTaskCard=(id:string)=>request(`/api/content-bank/tasks/${id}`);
export const putMethodology=(versionId:string,payload:unknown):Promise<Methodology>=>request(`/api/content-bank/task-versions/${versionId}/methodology`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
export type Attachment={id:string;filename:string;mime_type:"image/png"|"image/jpeg"|"image/webp";size_bytes:number;created_at:string;role:string|null;url:string};
export const listTaskAttachments=(versionId:string):Promise<{items:Attachment[]}>=>(request(`/api/content-bank/task-versions/${enc(versionId)}/attachments`));
export const uploadTaskAttachment=(versionId:string,role:string,file:File):Promise<Attachment>=>request(`/api/content-bank/task-versions/${enc(versionId)}/attachments?role=${enc(role)}`,{method:"POST",headers:{"Content-Type":file.type,"X-Filename":file.name},body:file});
export const submitReview=(taskId:string,versionNo:number)=>request(`/api/content-bank/tasks/${taskId}/versions/${versionNo}/submit-review`,json({}));
export const returnToDraft=(taskId:string,versionNo:number,reason:string)=>request(`/api/content-bank/tasks/${taskId}/versions/${versionNo}/return-to-draft`,json({reason}));
export const approveVersion=(taskId:string,versionNo:number)=>request(`/api/content-bank/tasks/${taskId}/versions/${versionNo}/approve`,json({}));
export const archiveTask=(taskId:string,reason?:string)=>request(`/api/content-bank/tasks/${taskId}/archive`,reason?json({reason}):{method:"POST"});

export type DuplicateReason="exact_statement"|"high_statement_similarity"|"same_primary_skill"|"same_final_answer";
export type DuplicateCandidate={task_id:string;task_version_id:string;version_no:number;title:string|null;status:"draft"|"review"|"approved";statement:string;statement_similarity:number;same_primary_skill:boolean;same_final_answer:boolean;reasons:DuplicateReason[]};
export type DuplicateCheckResponse={has_likely_duplicates:boolean;items:DuplicateCandidate[]};
export function checkTaskDuplicates(payload:{statement:string;primary_skill_id:string;final_answer:string|null;exclude_task_id:string|null;limit:number},signal?:AbortSignal):Promise<DuplicateCheckResponse>{return request("/api/content-bank/task-versions/check-duplicates",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal})}

export type TagCategory={code:string;name:string;sort_order:number};
export type TagSubject={id:string;code?:string;name:string};
export type TagRef={id:string;name:string;category_code:string;subject_id:string|null;status:"active"|"deprecated";replacement?:TagRef|null};
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
export type VersionTagsResponse={task_id:string;task_version_id:string;version_no:number;updated_at:string;tags:TagRef[]};
export const putVersionTags=(versionId:string,tag_ids:string[],expected_updated_at:string):Promise<VersionTagsResponse>=>request(`/api/content-bank/task-versions/${enc(versionId)}/tags`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({tag_ids,expected_updated_at})});

export type AssessmentStatus="draft"|"published";
export type AssessmentSummary={id:string;title:string;description:string|null;status:AssessmentStatus;variant_count:number;created_at:string;updated_at:string;published_at:string|null;published_by:string|null};
export type AssessmentItem={id:string;task_version_id:string;position:number;points:string};
export type AssessmentVariant={id:string;name:string;position:number;items:AssessmentItem[];total_points:string};
export type Assessment={id:string;title:string;description:string|null;status:AssessmentStatus;variants:AssessmentVariant[];created_at:string;updated_at:string;published_at:string|null;published_by:string|null};
export type AssessmentListPage={items:AssessmentSummary[];total:number;offset:number;limit:number};
export type ContentBankTask={task_id:string;latest_version_id:string;version_no:number;title:string|null;statement:string;subject_id:string;subject_name:string;grade_id:string;grade_name:string;topic_id:string;topic_name:string;task_type:string;difficulty:number;status:string};
export type ContentBankTaskPage={items:ContentBankTask[];total:number;offset:number;limit:number};
const assessmentRoot="/api/assessment-core/assessments";
const body=(method:string,value:unknown):RequestInit=>({method,headers:{"Content-Type":"application/json"},body:JSON.stringify(value)});
export const listAssessments=(status?:AssessmentStatus,offset=0,limit=20,signal?:AbortSignal):Promise<AssessmentListPage>=>{const p=new URLSearchParams({offset:String(offset),limit:String(limit)});if(status)p.set("status",status);return request(`${assessmentRoot}?${p}`,{signal})};
export const createAssessment=(value:{title:string;description:string|null}):Promise<Assessment>=>request(assessmentRoot,body("POST",value));
export const getAssessment=(id:string,signal?:AbortSignal):Promise<Assessment>=>request(`${assessmentRoot}/${enc(id)}`,{signal});
export const patchAssessment=(id:string,value:{expected_updated_at:string;title?:string;description?:string|null}):Promise<Assessment>=>request(`${assessmentRoot}/${enc(id)}`,body("PATCH",value));
export const createAssessmentVariant=(id:string,name:string):Promise<AssessmentVariant>=>request(`${assessmentRoot}/${enc(id)}/variants`,body("POST",{name}));
export const deleteAssessmentVariant=(id:string,variantId:string):Promise<void>=>request(`${assessmentRoot}/${enc(id)}/variants/${enc(variantId)}`,{method:"DELETE"});
export const addAssessmentItem=(id:string,variantId:string,value:{task_version_id:string;points:string}):Promise<AssessmentItem>=>request(`${assessmentRoot}/${enc(id)}/variants/${enc(variantId)}/items`,body("POST",value));
export const deleteAssessmentItem=(id:string,variantId:string,itemId:string):Promise<void>=>request(`${assessmentRoot}/${enc(id)}/variants/${enc(variantId)}/items/${enc(itemId)}`,{method:"DELETE"});
export const reorderAssessmentItems=(id:string,variantId:string,item_ids:string[],expected_updated_at:string):Promise<AssessmentVariant>=>request(`${assessmentRoot}/${enc(id)}/variants/${enc(variantId)}/item-order`,body("PUT",{item_ids,expected_updated_at}));
export const patchAssessmentItem=(id:string,variantId:string,itemId:string,points:string,expected_updated_at:string):Promise<AssessmentItem>=>request(`${assessmentRoot}/${enc(id)}/variants/${enc(variantId)}/items/${enc(itemId)}`,body("PATCH",{points,expected_updated_at}));
export const searchApprovedTasks=(params:URLSearchParams,signal?:AbortSignal):Promise<ContentBankTaskPage>=>{const approved=new URLSearchParams(params);approved.set("status","approved");return request(`/api/content-bank/tasks?${approved}`,{signal})};
export type AssessmentClassGroupSummary={id:string;name:string;active_student_count:number};
export type AssessmentClassGroupPage={items:AssessmentClassGroupSummary[];total:number;offset:number;limit:number};
export type TeacherAssignmentSummary={id:string;assessment_id:string;class_group_id:string;class_group_name:string;status:"open"|"closed";start_at:string;due_at:string;max_attempts:number;participant_count:number;created_at:string;closed_at:string|null};
export type TeacherAssignmentPage={items:TeacherAssignmentSummary[];total:number;offset:number;limit:number};
export type TeacherAssignment=Omit<TeacherAssignmentSummary,"class_group_name">&{participant_ids:string[]};
export type PublicationResponse={assessment:Assessment;assignment:TeacherAssignment};
export const listAssessmentClassGroups=(offset=0,limit=20,signal?:AbortSignal):Promise<AssessmentClassGroupPage>=>request(`/api/assessment-core/class-groups?${new URLSearchParams({offset:String(offset),limit:String(limit)})}`,{signal});
export const listAssessmentAssignments=(id:string,offset=0,limit=20,signal?:AbortSignal):Promise<TeacherAssignmentPage>=>request(`${assessmentRoot}/${enc(id)}/assignments?${new URLSearchParams({offset:String(offset),limit:String(limit)})}`,{signal});
export const publishAndAssignAssessment=(id:string,value:{class_group_id:string;start_at:string;due_at:string;max_attempts:number}):Promise<PublicationResponse>=>request(`${assessmentRoot}/${enc(id)}/publish-and-assign`,body("POST",value));
export const getTeacherAssignment=(id:string,signal?:AbortSignal):Promise<TeacherAssignment>=>request(`/api/assessment-core/assignments/${enc(id)}`,{signal});
export const closeTeacherAssignment=(id:string):Promise<TeacherAssignment>=>request(`/api/assessment-core/assignments/${enc(id)}/close`,body("POST",{}));

export type StudentAssignmentSummary={assignment_id:string;assessment_id:string;title:string;status:"open"|"closed";start_at:string;due_at:string;max_attempts:number;assigned_variant_id:string|null;attempt_count:number};
export type StudentAssignmentPage={items:StudentAssignmentSummary[];total:number;offset:number;limit:number};
export type StudentAssignmentDetail=StudentAssignmentSummary&{description:string|null;participant_id:string;current_draft_attempt_id:string|null;submitted_attempt_count:number;submitted_attempts:{id:string;attempt_no:number;submitted_at:string}[]};
export type StudentExecutionItem={id:string;task_version_id:string;position:number;points:string;title:string|null;statement:string;task_type:string;answer_format:"single_choice"|"multiple_choice"|"short_text"|"number"|"expression"|"long_text"};
export type StudentRawAnswer=string|string[]|null|boolean|number|Record<string,unknown>;
export type StudentAnswer={item_id:string;raw_answer:StudentRawAnswer;normalized_answer:unknown;created_at:string;updated_at:string};
export type StudentSubmission={id:string;attempt_no:number;status:"draft"|"submitted";assigned_variant_id:string;resumed:boolean;started_at:string;submitted_at:string|null;answers:StudentAnswer[];items:StudentExecutionItem[]};
const studentRoot="/api/assessment-core/student";
export const listAnswerAttachments=(submissionId:string,itemId:string):Promise<{items:Attachment[]}>=>(request(`${studentRoot}/attempts/${enc(submissionId)}/answers/${enc(itemId)}/attachments`));
export const uploadAnswerAttachment=(submissionId:string,itemId:string,file:File):Promise<Attachment>=>request(`${studentRoot}/attempts/${enc(submissionId)}/answers/${enc(itemId)}/attachments`,{method:"POST",headers:{"Content-Type":file.type,"X-Filename":file.name},body:file});
export const listStudentAssignments=(offset=0,limit=20,signal?:AbortSignal):Promise<StudentAssignmentPage>=>request(`${studentRoot}/assignments?${new URLSearchParams({offset:String(offset),limit:String(limit)})}`,{signal});
export const getStudentAssignment=(id:string,signal?:AbortSignal):Promise<StudentAssignmentDetail>=>request(`${studentRoot}/assignments/${enc(id)}`,{signal});
export const startStudentAttempt=(assignmentId:string,key:string):Promise<StudentSubmission>=>request(`${studentRoot}/assignments/${enc(assignmentId)}/attempts/start`,{method:"POST",headers:{"Content-Type":"application/json","Idempotency-Key":key},body:"{}"});
export const getStudentAttempt=(submissionId:string,signal?:AbortSignal):Promise<StudentSubmission>=>request(`${studentRoot}/attempts/${enc(submissionId)}`,{signal});
export const putStudentAnswer=(submissionId:string,itemId:string,raw_answer:StudentRawAnswer,expected_updated_at:string|null):Promise<StudentAnswer>=>request(`${studentRoot}/attempts/${enc(submissionId)}/answers/${enc(itemId)}`,body("PUT",{raw_answer,expected_updated_at}));
export const deleteStudentAnswer=(submissionId:string,itemId:string):Promise<void>=>request(`${studentRoot}/attempts/${enc(submissionId)}/answers/${enc(itemId)}`,{method:"DELETE"});
export const submitStudentAttempt=(submissionId:string,key:string):Promise<StudentSubmission>=>request(`${studentRoot}/attempts/${enc(submissionId)}/submit`,{method:"POST",headers:{"Content-Type":"application/json","Idempotency-Key":key},body:"{}"});

export type StudentCommand="start"|"submit";
export function pendingCommandKey(command:StudentCommand,id:string):string{const storageKey=`assessment:${command}:${id}`;let key=sessionStorage.getItem(storageKey);if(!key){key=`${command}:${crypto.randomUUID()}`;sessionStorage.setItem(storageKey,key)}return key}
export const clearPendingCommandKey=(command:StudentCommand,id:string)=>sessionStorage.removeItem(`assessment:${command}:${id}`);
