// @vitest-environment jsdom
import {afterEach,describe,expect,it,vi} from "vitest";
import {cleanup,render,screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {FolderBrowser} from "./FolderBrowser";
const response=(body:unknown)=>Promise.resolve(new Response(JSON.stringify(body),{headers:{"Content-Type":"application/json"}}));
const subject={id:"s1",name:"Математика"},folder={id:"f1",subject_id:"s1",parent_id:null,name:"Алгебра",depth:1,created_at:"2026-01-01T00:00:00Z",updated_at:"2026-01-02T00:00:00Z"};
const level={subject,folder:null,breadcrumb:[],folders:[folder],tasks:{items:[],total:0,offset:0,limit:20},level_task_total:0,subject_task_total:0};
function api(){return vi.fn((input:RequestInfo|URL)=>String(input).includes("folders/tree")?response({subject,folders:[{...folder,children:[]}]}):String(input).includes("/catalog/")?response({items:[]}):response(level))}
afterEach(()=>{cleanup();vi.restoreAllMocks()});
describe("Content Bank folder cleanup",()=>{
 it("opens a folder and preserves the existing route",async()=>{vi.stubGlobal("fetch",api());const navigate=vi.fn();render(<FolderBrowser subjectId="s1" navigate={navigate}/>);const link=await screen.findByRole("link",{name:/Алгебра/});expect(link.getAttribute("href")).toContain("/content-bank/subjects/s1/folders/f1");await userEvent.click(link);expect(navigate).toHaveBeenCalledWith("/content-bank/subjects/s1/folders/f1")});
 it("does not render the legacy folder action menu",async()=>{vi.stubGlobal("fetch",api());render(<FolderBrowser subjectId="s1" navigate={vi.fn()}/>);await screen.findByRole("link",{name:/Алгебра/});expect(screen.queryByRole("button",{name:"Переименовать"})).toBeNull();expect(screen.queryByRole("button",{name:"Удалить"})).toBeNull();expect(screen.queryByRole("button",{name:"Переместить папку"})).toBeNull();expect(screen.getByRole("button",{name:"Новая папка"})).toBeTruthy()});
});

describe("Content Bank subject roots",()=>{
 it("renders active subjects without a provisional badge",async()=>{
  vi.stubGlobal("fetch",vi.fn(()=>response({items:[{id:"active-id",name:"Физика",status:"active"}]})));
  render(<FolderBrowser navigate={vi.fn()}/>);
  const link=await screen.findByRole("link",{name:/Физика/});
  expect(link.getAttribute("href")).toBe("/content-bank/subjects/active-id");
  expect(screen.queryByText("ПРЕДЛОЖЕНО")).toBeNull();
  expect(link.querySelector(".file-icon")).toBeTruthy();
 });
 it("marks a provisional subject and keeps its subject route clickable",async()=>{
  vi.stubGlobal("fetch",vi.fn(()=>response({items:[{id:"provisional-id",name:"Математика",status:"provisional"}]})));
  const navigate=vi.fn();
  render(<FolderBrowser navigate={navigate}/>);
  const link=await screen.findByRole("link",{name:/Математика/});
  const badge=screen.getByText("ПРЕДЛОЖЕНО");
  expect(badge.classList.contains("badge")).toBe(true);
  expect(badge.classList.contains("provisional-badge")).toBe(true);
  expect(link.querySelector(".file-icon")).toBeTruthy();
  expect(link.getAttribute("href")).toBe("/content-bank/subjects/provisional-id");
  await userEvent.click(link);
  expect(navigate).toHaveBeenCalledWith("/content-bank/subjects/provisional-id");
 });
});
