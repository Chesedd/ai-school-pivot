import {request} from "./api";
export type TeacherNote={id:string;body:string;teacher_user_id:string;teacher_display_name:string;class_group_id:string;student_id:string|null;created_at:string;updated_at:string};
export type NotePage={items:TeacherNote[];total:number;offset:number;limit:number};
const e=encodeURIComponent;
const base=(classId:string,studentId?:string)=>`/api/classrooms/${e(classId)}${studentId?`/students/${e(studentId)}`:""}/notes`;
export const listNotes=(classId:string,studentId?:string,signal?:AbortSignal):Promise<NotePage>=>request(`${base(classId,studentId)}?offset=0&limit=50`,{signal});
export const createNote=(classId:string,body:string,studentId?:string):Promise<TeacherNote>=>request(base(classId,studentId),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({body})});
export const updateNote=(classId:string,note:TeacherNote,body:string,studentId?:string):Promise<TeacherNote>=>request(`${base(classId,studentId)}/${e(note.id)}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({body,expected_updated_at:note.updated_at})});
export const deleteNote=(classId:string,noteId:string,studentId?:string):Promise<void>=>request(`${base(classId,studentId)}/${e(noteId)}`,{method:"DELETE"});
