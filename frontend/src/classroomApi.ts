import {request} from "./api";
export type ClassroomStatus="active"|"archived"|"all";
export type TeacherClass={id:string;name:string;grade_id:string|null;grade_number:number|null;grade_name:string|null;archived_at:string|null;active_student_count:number};
export type TeacherClassPage={items:TeacherClass[];total:number;offset:number;limit:number};
export type TeacherStudent={id:string;display_name:string;external_ref:string|null;archived_at:string|null};
const enc=encodeURIComponent;
export function listMyClasses(status:ClassroomStatus="active",offset=0,limit=20,signal?:AbortSignal):Promise<TeacherClassPage>{const params=new URLSearchParams({status,offset:String(offset),limit:String(limit)});return request(`/api/classrooms?${params}`,{signal})}
export const getMyClass=(id:string,signal?:AbortSignal):Promise<TeacherClass>=>request(`/api/classrooms/${enc(id)}`,{signal});
export const listMyClassStudents=(id:string,status:ClassroomStatus="active",signal?:AbortSignal):Promise<TeacherStudent[]>=>request(`/api/classrooms/${enc(id)}/students?${new URLSearchParams({status})}`,{signal});
