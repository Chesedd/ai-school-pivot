import {afterEach,describe,expect,it,vi} from "vitest";
import {cleanup,render,screen,waitFor} from "@testing-library/react";
import {App} from "./main";
import * as apiClient from "./api";

const json=(body:unknown)=>new Response(JSON.stringify(body),{status:200,headers:{"Content-Type":"application/json"}});
afterEach(()=>{cleanup();vi.restoreAllMocks();history.replaceState({},"","/")});

describe("Content Bank application shell",()=>{
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
 it("supports the direct import route with step semantics and one h1",()=>{history.replaceState({},"","/content-bank/import");render(<App/>);expect(screen.getByRole("link",{name:"Импорт"}).getAttribute("aria-current")).toBe("page");expect(screen.getByRole("list",{name:"Этапы импорта"}).querySelector('[aria-current="step"]')?.textContent).toContain("1. Файл");expect(screen.getAllByRole("heading",{level:1})).toHaveLength(1)});
 it("supports the direct task-card route",async()=>{history.replaceState({},"","/content-bank/tasks/missing");vi.spyOn(apiClient,"getTaskCard").mockRejectedValue(new apiClient.ApiError(404,"not_found","missing"));render(<App/>);expect(await screen.findByRole("heading",{name:"Задание не найдено"})).toBeTruthy();expect(screen.getAllByRole("heading",{level:1})).toHaveLength(1)});
});
