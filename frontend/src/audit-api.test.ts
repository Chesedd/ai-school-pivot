import {afterEach,describe,expect,it,vi} from "vitest";
import {getTaskAudit} from "./api";
afterEach(()=>vi.restoreAllMocks());
describe("getTaskAudit",()=>{
 const ok=()=>new Response(JSON.stringify({items:[],total:0,offset:0,limit:20}),{status:200});
 it("omits action and encodes task id",async()=>{const f=vi.spyOn(globalThis,"fetch").mockResolvedValue(ok());await getTaskAudit("task/id",{offset:0,limit:20});expect(f.mock.calls[0][0]).toBe("http://localhost:8000/api/content-bank/tasks/task%2Fid/audit?offset=0&limit=20")});
 it("adds action and pagination",async()=>{const f=vi.spyOn(globalThis,"fetch").mockResolvedValue(ok());await getTaskAudit("id",{offset:20,limit:20,action:"returned_to_draft"});expect(f.mock.calls[0][0]).toBe("http://localhost:8000/api/content-bank/tasks/id/audit?offset=20&limit=20&action=returned_to_draft")});
 it("uses shared errors",async()=>{vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response(JSON.stringify({error:{code:"audit_error",message:"Ошибка аудита"}}),{status:500}));await expect(getTaskAudit("id")).rejects.toMatchObject({status:500,code:"audit_error",message:"Ошибка аудита"})});
});
