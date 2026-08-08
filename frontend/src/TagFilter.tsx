import {useEffect,useRef,useState} from "react";
import {ApiError,getTag,type TagRef} from "./api";
import {TagPicker} from "./TagPicker";

export const uniqueTagIds=(values:string[])=>[...new Set(values.filter(Boolean))];
export function normalizeTagParams(params:URLSearchParams){const ids=uniqueTagIds(params.getAll("tag_id"));params.delete("tag_id");ids.forEach(id=>params.append("tag_id",id));return ids}
const placeholder=(id:string):TagRef=>({id,name:"Загрузка тега…",category_code:"",subject_id:null,status:"active"});

export function TagFilter({ids,subjectId,onChange}:{ids:string[];subjectId?:string;onChange:(tags:TagRef[])=>void}){
 const normalized=uniqueTagIds(ids),key=normalized.join("|"),[tags,setTags]=useState<TagRef[]>(()=>normalized.map(placeholder)),[message,setMessage]=useState(""),resolved=useRef(new Map<string,TagRef>()),requested=useRef(new Set<string>());
 useEffect(()=>{let cancelled=false;setMessage("");setTags(normalized.map(id=>resolved.current.get(id)??placeholder(id)));for(const id of normalized){if(resolved.current.has(id)||requested.current.has(id))continue;requested.current.add(id);getTag(id).then(tag=>{if(cancelled)return;const ref:TagRef={id:tag.id,name:tag.name,category_code:tag.category.code,subject_id:tag.subject?.id??null,status:tag.status,replacement:tag.replacement};resolved.current.set(id,ref);setTags(current=>current.map(x=>x.id===id?ref:x))}).catch(error=>{if(cancelled)return;const unknown:TagRef={id,name:"Недоступный тег",category_code:"",subject_id:null,status:"deprecated"};resolved.current.set(id,unknown);setTags(current=>current.map(x=>x.id===id?unknown:x));setMessage(error instanceof ApiError&&error.code==="tag_not_found"?"Один из тегов из ссылки больше не существует.":"Не удалось восстановить один из тегов из ссылки.")})}return()=>{cancelled=true}},[key]);
 return <div className="tag-filter"><TagPicker label="Теги" subjectId={subjectId} value={tags} onChange={next=>{setTags(next);onChange(next)}}/><p className="help-text">Задания должны содержать все выбранные теги.</p>{message&&<p className="field-error" role="alert">{message}</p>}</div>
}
