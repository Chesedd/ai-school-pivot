import {afterEach,describe,expect,it,vi} from "vitest";
import {cleanup,render,screen,waitFor} from "@testing-library/react";
import {App} from "./main";
import * as apiClient from "./api";

const json=(body:unknown)=>new Response(JSON.stringify(body),{status:200,headers:{"Content-Type":"application/json"}});
afterEach(()=>{cleanup();vi.restoreAllMocks();history.replaceState({},"","/")});

describe("Content Bank application shell",()=>{
 it("shows Classes only with classroom.admin and protects direct navigation",()=>{const base={user_id:"u",login:"u",display_name:"User",roles:["teacher"],student_id:null};history.replaceState({},"","/admin/classes");const view=render(<App principal={{...base,capabilities:["classroom.admin"]}}/>);expect(screen.getByRole("link",{name:"Классы"}).getAttribute("aria-current")).toBe("page");view.rerender(<App principal={{...base,capabilities:["users.manage"]}}/>);expect(screen.queryByRole("link",{name:"Классы"})).toBeNull();expect(screen.getByRole("heading",{name:"Нет доступа"})).toBeTruthy()});
 it("does not expose Classes to student principals",()=>{history.replaceState({},"","/student/assignments");vi.spyOn(apiClient,"listStudentAssignments").mockResolvedValue({items:[],total:0,offset:0,limit:20});render(<App principal={{user_id:"s",login:"s",display_name:"Student",roles:["student"],student_id:"s",capabilities:["student.assignments.read"]}}/>);expect(screen.queryByRole("link",{name:"Классы"})).toBeNull()});
 it("provides a skip link, semantic navigation, active item, and one page heading",async()=>{
  history.replaceState({},"","/content-bank");
  vi.spyOn(globalThis,"fetch").mockImplementation(()=>Promise.resolve(json({items:[]})));
  render(<App/>);
  expect(screen.getByRole("link",{name:"Перейти к содержимому"}).getAttribute("href")).toBe("#main-content");
  expect(screen.getByRole("navigation",{name:"Основная навигация"})).toBeTruthy();
  expect(screen.getByRole("link",{name:"Задания"}).getAttribute("aria-current")).toBe("page");
  await waitFor(()=>expect(screen.getAllByRole("heading",{level:1})).toHaveLength(1));
 });
 it("supports the direct create route and marks required native controls",async()=>{
  history.replaceState({},"","/content-bank/new");
  vi.spyOn(globalThis,"fetch").mockImplementation(()=>Promise.resolve(json({items:[]})));
  render(<App/>);
  expect(screen.getByRole("link",{name:"Создать задание"}).getAttribute("aria-current")).toBe("page");
  await waitFor(()=>expect(screen.getAllByRole("heading",{level:1})).toHaveLength(1));
  expect((screen.getByLabelText(/Условие/) as HTMLTextAreaElement).required).toBe(true);
 });
 it.each(["/content-bank/import","/content-bank/authoring/new","/content-bank/authoring/sessions/old/workspace"])("does not expose obsolete route %s",path=>{history.replaceState({},"",path);render(<App/>);expect(screen.queryByRole("link",{name:/Импорт|AI-авторинг/})).toBeNull();expect(screen.getByRole("heading",{name:"Страница не найдена"})).toBeTruthy()});
 it("supports the direct task-card route",async()=>{history.replaceState({},"","/content-bank/tasks/missing");vi.spyOn(apiClient,"getTaskCard").mockRejectedValue(new apiClient.ApiError(404,"not_found","missing"));render(<App/>);expect(await screen.findByRole("heading",{name:"Задание не найдено"})).toBeTruthy();expect(screen.getAllByRole("heading",{level:1})).toHaveLength(1)});
 it.each(["/student","/student/assignments"])("routes %s to the student list and activates its navigation",async path=>{history.replaceState({},"",path);vi.spyOn(apiClient,"listStudentAssignments").mockResolvedValue({items:[],total:0,offset:0,limit:20});render(<App/>);expect(await screen.findByRole("heading",{name:"Мои работы"})).toBeTruthy();expect(screen.getByRole("link",{name:"Мои работы"}).getAttribute("aria-current")).toBe("page")});
 it("routes student assignment detail",async()=>{history.replaceState({},"","/student/assignments/a1");vi.spyOn(apiClient,"getStudentAssignment").mockRejectedValue(new apiClient.ApiError(404,"assignment_not_found","missing"));render(<App/>);expect(await screen.findByText("Работа или попытка не найдена.")).toBeTruthy();expect(screen.getByRole("link",{name:"Мои работы"}).getAttribute("aria-current")).toBe("page")});
 it("routes a direct student attempt URL",async()=>{history.replaceState({},"","/student/assignments/a1/attempts/s1");vi.spyOn(apiClient,"getStudentAssignment").mockRejectedValue(new apiClient.ApiError(404,"assignment_not_found","missing"));vi.spyOn(apiClient,"getStudentAttempt").mockRejectedValue(new apiClient.ApiError(404,"submission_not_found","missing"));render(<App/>);expect(await screen.findByRole("heading",{name:"Попытка"})).toBeTruthy();expect(screen.getByRole("link",{name:"Мои работы"}).getAttribute("aria-current")).toBe("page")});
});
