import {request} from "../api";

export type CatalogProposalRequest=
 | {kind:"subject";name:string}
 | {kind:"grade";number:number;name:string}
 | {kind:"topic";name:string;subject_id:string;grade_id:string}
 | {kind:"subtopic";name:string;topic_id:string}
 | {kind:"skill";name:string;subtopic_id:string};

export type CatalogProposalResponse={kind:"subject"|"grade"|"topic"|"subtopic"|"skill";id:string;name:string;status:"active"|"provisional";outcome:"existing_active"|"existing_provisional"|"created_provisional";number:number|null;subject_id:string|null;grade_id:string|null;topic_id:string|null;subtopic_id:string|null};

export const proposeCatalogValue=(value:CatalogProposalRequest):Promise<CatalogProposalResponse>=>request("/api/catalog/proposals",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(value)});
