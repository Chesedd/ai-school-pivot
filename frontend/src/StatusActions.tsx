import {useEffect,useRef,useState,type ReactNode} from "react";
import type {ValidationIssue} from "./api";

export type StatusAction="submit"|"return"|"approve"|"archive";

export function ValidationIssueList({title,issues,kind="error",description}:{title:string;issues:ValidationIssue[];kind?:"warning"|"error";description?:string}){
 return <section className={`validation-report ${kind}`} role={kind==="error"?"alert":undefined} aria-live={kind==="warning"?"polite":undefined}><h2>{title}</h2>{description&&<p>{description}</p>}{issues.length?<ul>{issues.map((x,i)=><li key={`${x.field}-${x.code}-${i}`}><strong>{x.message}</strong></li>)}</ul>:<p>Замечаний нет.</p>}</section>
}

export function Dialog({title,children,onClose,busy}:{title:string;children:ReactNode;onClose:()=>void;busy:boolean}){
 const box=useRef<HTMLDivElement>(null),previous=useRef<HTMLElement|null>(null);
 useEffect(()=>{previous.current=document.activeElement as HTMLElement;const focusables=()=>Array.from(box.current?.querySelectorAll<HTMLElement>('button:not([disabled]), textarea:not([disabled]), input:not([disabled])')??[]);focusables()[0]?.focus();const key=(e:KeyboardEvent)=>{if(e.key==="Escape"&&!busy){e.preventDefault();onClose()}if(e.key==="Tab"){const f=focusables();if(!f.length)return;const first=f[0],last=f[f.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}};addEventListener("keydown",key);return()=>{removeEventListener("keydown",key);previous.current?.focus()}},[busy,onClose]);
 return <div className="dialog-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget&&!busy)onClose()}}><div ref={box} className="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><h2 id="dialog-title">{title}</h2>{children}</div></div>
}

export function StatusActionBar({status,archived,dirty,busy,methodologySaving,onAction}:{status:string;archived:boolean;dirty:boolean;busy:StatusAction|null;methodologySaving:boolean;onAction:(action:StatusAction)=>void}){
 const disabled=dirty||!!busy||methodologySaving;
 const button=(action:StatusAction,label:string,loading:string,kind="")=><button className={kind} disabled={disabled} onClick={()=>onAction(action)}>{busy===action?loading:label}</button>;
 return <section className="status-bar" aria-labelledby="status-title"><div><h2 id="status-title">Статус задания</h2><p><span className="badge">{status}</span></p>{archived&&<p>Архивная карточка доступна только для чтения.</p>}{dirty&&<p className="dirty-hint">Сначала сохраните или отмените изменения методической структуры</p>}{methodologySaving&&<p>Дождитесь завершения сохранения методики.</p>}</div>{!archived&&status!=="archived"&&<div className="status-buttons">{status==="draft"&&button("submit","Отправить на проверку","Отправка на проверку…")}{status==="review"&&button("return","Вернуть в черновик","Возврат в черновик…","secondary")}{status==="review"&&button("approve","Утвердить","Утверждение…")}{button("archive","Архивировать задание","Архивирование…","danger")}</div>}</section>
}

export function ReasonDialog({mode,busy,onClose,onSubmit}:{mode:"return"|"archive";busy:boolean;onClose:()=>void;onSubmit:(reason:string)=>void}){const [reason,setReason]=useState("");const required=mode==="return",trimmed=reason.trim();return <Dialog title={required?"Вернуть в черновик":"Архивировать задание"} onClose={onClose} busy={busy}>{required?<p>Укажите причину возврата. Она будет сохранена в журнале аудита.</p>:<p>После архивирования задание будет доступно только для чтения.</p>}<label>Причина {required?"":"(необязательно)"}<textarea autoFocus maxLength={1000} value={reason} onChange={e=>setReason(e.target.value)}/><span className="char-count">{reason.length} / 1000</span></label><div className="dialog-actions"><button className="secondary" disabled={busy} onClick={onClose}>Отмена</button><button className={required?"":"danger"} disabled={busy||(required&&!trimmed)} onClick={()=>onSubmit(trimmed)}>{busy?(required?"Возврат…":"Архивирование…"):(required?"Вернуть в черновик":"Архивировать задание")}</button></div></Dialog>}
