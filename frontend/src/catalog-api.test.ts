import {afterEach,describe,expect,it,vi} from "vitest";
import {getCatalog,getSubjectRoots} from "./api";

const response=()=>Promise.resolve(new Response(JSON.stringify({items:[]}),{headers:{"Content-Type":"application/json"}}));
afterEach(()=>vi.restoreAllMocks());

describe("catalog and navigation API boundaries",()=>{
 it("loads subject roots from the navigation-specific endpoint",async()=>{
  const fetch=vi.fn(response);vi.stubGlobal("fetch",fetch);
  await getSubjectRoots();
  expect(String(fetch.mock.calls[0][0])).toContain("/api/content-bank/navigation/subjects");
 });
 it("keeps generic subject selectors on the active-only catalog endpoint",async()=>{
  const fetch=vi.fn(response);vi.stubGlobal("fetch",fetch);
  await getCatalog("subjects");
  expect(String(fetch.mock.calls[0][0])).toContain("/api/content-bank/catalog/subjects");
 });
});
