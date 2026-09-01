import {afterEach,describe,expect,it,vi} from "vitest";
import {ApiError} from "../api";
import {proposeCatalogValue} from "./catalogProposals";
const response=(body:unknown,status:number)=>new Response(JSON.stringify(body),{status,headers:{"Content-Type":"application/json"}});
const base={kind:"subject",id:"00000000-0000-0000-0000-000000000001",name:"Алгебра",number:null,subject_id:null,grade_id:null,topic_id:null,subtopic_id:null};
afterEach(()=>vi.restoreAllMocks());
describe("proposeCatalogValue",()=>{
 it.each([[201,"created_provisional","provisional"],[200,"existing_provisional","provisional"],[200,"existing_active","active"]] as const)("parses %s %s",async(status,outcome,catalogStatus)=>{vi.spyOn(globalThis,"fetch").mockResolvedValue(response({...base,status:catalogStatus,outcome},status));expect(await proposeCatalogValue({kind:"subject",name:"Алгебра"})).toMatchObject({outcome,status:catalogStatus,id:base.id});expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/catalog/proposals"),expect.objectContaining({credentials:"include",method:"POST",body:JSON.stringify({kind:"subject",name:"Алгебра"})}))});
 it("preserves the bounded API error envelope",async()=>{vi.spyOn(globalThis,"fetch").mockResolvedValue(response({error:{code:"catalog_parent_deprecated",message:"Parent is deprecated",details:[]}},409));await expect(proposeCatalogValue({kind:"subtopic",name:"X",topic_id:"t"})).rejects.toMatchObject<ApiError>({status:409,code:"catalog_parent_deprecated",message:"Parent is deprecated"})});
});
