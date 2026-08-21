import {afterEach,describe,expect,it,vi} from "vitest";
import {approveVersion,archiveTask,returnToDraft,submitReview} from "./api";

afterEach(()=>vi.restoreAllMocks());
describe("status API",()=>{
 it.each([
  ["submit",()=>submitReview("task",3),"/api/content-bank/tasks/task/versions/3/submit-review",{}],
  ["return",()=>returnToDraft("task",3,"reason"),"/api/content-bank/tasks/task/versions/3/return-to-draft",{reason:"reason"}],
  ["approve",()=>approveVersion("task",3),"/api/content-bank/tasks/task/versions/3/approve",{}],
 ])("sends %s command to its contract endpoint",async(_,call,path,body)=>{const fetch=vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response("{}",{status:200}));await call();expect(fetch).toHaveBeenCalledTimes(1);const [url,init]=fetch.mock.calls[0];expect(String(url)).toContain(path);expect(JSON.parse(String(init?.body))).toEqual(body)});
 it("archives without a body",async()=>{const fetch=vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response("{}",{status:200}));await archiveTask("task");expect(fetch.mock.calls[0][1]?.body).toBeUndefined()});
 it("archives with a reason",async()=>{const fetch=vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response("{}",{status:200}));await archiveTask("task","why");expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({reason:"why"})});
});
