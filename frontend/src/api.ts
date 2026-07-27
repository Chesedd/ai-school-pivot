import type {Methodology} from "./methodology";
export const apiBase=import.meta.env.VITE_API_BASE_URL??"http://localhost:8000";
export class ApiError extends Error{constructor(public status:number,public code:string,message:string,public details:any[]=[]){super(message)}}
async function request(path:string,init?:RequestInit){const response=await fetch(`${apiBase}${path}`,init);let data:any={};try{data=await response.json()}catch{}if(!response.ok)throw new ApiError(response.status,data.error?.code??"unexpected_error",data.error?.message??"Ошибка API",data.error?.details??[]);return data}
export const getTaskCard=(id:string)=>request(`/api/content-bank/tasks/${id}`);
export const putMethodology=(versionId:string,payload:unknown):Promise<Methodology>=>request(`/api/content-bank/task-versions/${versionId}/methodology`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
