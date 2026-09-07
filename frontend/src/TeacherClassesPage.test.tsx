import {afterEach,describe,expect,it,vi} from "vitest";
import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {TeacherClassesPage} from "./TeacherClassesPage";
import {TeacherClassDetailPage} from "./TeacherClassDetailPage";

afterEach(()=>{cleanup();vi.restoreAllMocks()});
const json=(body:unknown,status=200)=>Promise.resolve(new Response(JSON.stringify(body),{status,headers:{"Content-Type":"application/json"}}));

describe("teacher classroom workspace",()=>{
 it("renders only the server-returned class summaries without mutation controls",async()=>{vi.spyOn(globalThis,"fetch").mockImplementation(()=>json({items:[{id:"7a",name:"7А",grade_id:"g7",grade_number:7,grade_name:"7 класс",archived_at:null,active_student_count:24},{id:"8b",name:"8Б",grade_id:"g8",grade_number:8,grade_name:"8 класс",archived_at:"2026-09-01T00:00:00Z",active_student_count:19}],total:2,offset:0,limit:20}));const navigate=vi.fn();render(<TeacherClassesPage navigate={navigate}/>);expect(await screen.findByText("7А")).toBeTruthy();expect(screen.getByText("8Б")).toBeTruthy();expect(screen.getByText("7 класс")).toBeTruthy();expect(screen.getByText("24")).toBeTruthy();expect(screen.queryByText(/назначить учителя|создать ученика|архивировать/i)).toBeNull();fireEvent.click(screen.getByText("7А"));expect(navigate).toHaveBeenCalledWith("/classrooms/7a")});
 it("loads class and roster as read-only data",async()=>{vi.spyOn(globalThis,"fetch").mockImplementation((input)=>String(input).includes("students")?json([{id:"s1",display_name:"Анна",external_ref:"A-1",archived_at:null}]):json({id:"7a",name:"7А",grade_id:"g7",grade_number:7,grade_name:"7 класс",archived_at:null,active_student_count:1}));render(<TeacherClassDetailPage classId="7a" navigate={vi.fn()}/>);expect(await screen.findByRole("heading",{name:"7А"})).toBeTruthy();expect(screen.getByText("Анна")).toBeTruthy();expect(screen.getByText("A-1")).toBeTruthy();expect(screen.queryByText(/самостоятельные работы|переместить|архивировать/i)).toBeNull()});
 it("renders the non-enumerating lost-access state after 404",async()=>{vi.spyOn(globalThis,"fetch").mockImplementation(()=>json({error:{code:"classroom_not_found",message:"not found"}},404));render(<TeacherClassDetailPage classId="foreign" navigate={vi.fn()}/>);expect(await screen.findByText("Класс не найден или больше вам не назначен.")).toBeTruthy()});
});
