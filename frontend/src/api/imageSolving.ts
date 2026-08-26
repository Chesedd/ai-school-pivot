import {request} from "../api";
export const IMAGE_SOLVING_MIME_TYPES=["image/png","image/jpeg","image/webp","application/pdf"] as const;
export const MAX_IMAGE_SOLVING_FILE_SIZE=25*1024*1024;
export type ImageSolvingStatus="created"|"extracting"|"extracted"|"solving"|"solved"|"validated"|"failed";
export type ImageSolvingSession={session_id:string;artifact_id:string;status:ImageSolvingStatus;stages?:{extraction:string;solver:string;validation:string};created_at?:string;updated_at?:string};
export type ImageSolvingResult={session_id:string;artifact_id:string;extraction:{extracted_text:string;structured_statement:string;task_classification:{task_type:string|null;answer_format:string|null};confidence:string|number;choices?:string[]|null};solution:{answer:string;reasoning_summary:string;confidence:string|number};validation:{status:string;findings:string[];manual_review:boolean}};
export type ImagePromotionRequest={title:string|null;statement:string;task_type:string;answer_format:string;difficulty:number;subject_id:string;grade_id:string;topic_id:string;subtopic_id:string|null;skill_ids:string[];solution:string;final_answer:string|null;review_confirmed:boolean;review_note:string|null;confirm_questionable:boolean};
export type ImagePromotionResponse={session_id:string;task_id:string;task_version_id:string;status:"draft";already_existing:boolean};
export type ImageSolvingAttempt={stage:"extraction"|"solver"|"validation";provider:string|null;model:string|null;usage:{input_tokens:number|null;output_tokens:number|null};cost:string|number|null;currency:string|null;latency_ms:number|null;request_id:string|null;created_at:string};
export function uploadArtifact(file:File):Promise<{artifact_id:string;mime_type:string;size_bytes:number;created_at:string}>{const form=new FormData();form.append("file",file);return request("/api/image-solving/artifacts",{method:"POST",body:form})}
export const createImageSolvingSession=(artifactId:string):Promise<ImageSolvingSession>=>request("/api/image-solving/sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({artifact_id:artifactId})});
export const runImageSolvingSession=(id:string):Promise<ImageSolvingSession>=>request(`/api/image-solving/sessions/${encodeURIComponent(id)}/run`,{method:"POST"});
export const getImageSolvingSession=(id:string,signal?:AbortSignal):Promise<ImageSolvingSession>=>request(`/api/image-solving/sessions/${encodeURIComponent(id)}`,{signal});
export const getImageSolvingResult=(id:string,signal?:AbortSignal):Promise<ImageSolvingResult>=>request(`/api/image-solving/sessions/${encodeURIComponent(id)}/result`,{signal});
export const getImageSolvingAttempts=(id:string,signal?:AbortSignal):Promise<{items:ImageSolvingAttempt[]}>=>(request(`/api/image-solving/sessions/${encodeURIComponent(id)}/attempts`,{signal}));
export const promoteImageSolvingSession=(id:string,value:ImagePromotionRequest):Promise<ImagePromotionResponse>=>request(`/api/image-solving/sessions/${encodeURIComponent(id)}/promote`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(value)});
