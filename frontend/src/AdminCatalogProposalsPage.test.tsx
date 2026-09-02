import {act,cleanup,render,screen,waitFor,within} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import * as api from "./api";
import {AdminCatalogProposalsPage} from "./AdminCatalogProposalsPage";
import {App} from "./main";

vi.mock("./api",async()=>({...await vi.importActual<typeof import("./api")>("./api"),request:vi.fn(),getSubjectRoots:vi.fn(),getCatalog:vi.fn()}));
const mocked=vi.mocked(api);
const proposal={id:"proposal-1",kind:"subject" as const,name:"Алгебра",status:"provisional" as const,proposed_by:"teacher-uuid"};
const principal=(capabilities:string[])=>({user_id:"admin",login:"admin",display_name:"Admin",roles:[],student_id:null,capabilities});
const list=()=>mocked.request.mockResolvedValueOnce([proposal]);

beforeEach(()=>{vi.resetAllMocks();list();mocked.getCatalog.mockResolvedValue({items:[{id:"canonical-1",name:"Математика"}]});});
afterEach(()=>{cleanup();history.replaceState({},"","/")});

describe("AdminCatalogProposalsPage",()=>{
 it("renders the queue and safe proposal context",async()=>{render(<AdminCatalogProposalsPage/>);expect(await screen.findByRole("heading",{name:"Предложения каталога"})).toBeTruthy();expect(await screen.findByText("Алгебра")).toBeTruthy();expect(screen.getByText("Предмет")).toBeTruthy();expect(screen.getByText("Предложил: teacher-uuid")).toBeTruthy();for(const name of ["Подтвердить","Объединить","Отклонить"])expect(screen.getByRole("button",{name})).toBeTruthy()});

 it("deduplicates confirm while pending and refreshes away the resolved row",async()=>{let finish!:(value:unknown)=>void,postCalls=0,resolved=false;mocked.request.mockReset().mockImplementation((path:string)=>{if(path.includes("/confirm")){postCalls++;return new Promise(r=>{finish=r})}return Promise.resolve(resolved?[]:[proposal])});render(<AdminCatalogProposalsPage/>);const button=await screen.findByRole("button",{name:"Подтвердить"});await act(async()=>{button.click();button.click()});expect(postCalls).toBe(1);expect((button as HTMLButtonElement).disabled).toBe(true);expect(mocked.request).toHaveBeenCalledWith("/api/catalog/proposals/subject/proposal-1/confirm",expect.objectContaining({method:"POST",body:"{}"}));resolved=true;await act(async()=>finish({}));expect(await screen.findByText("Новых предложений нет.")).toBeTruthy()});

 it("requires a merge reason and sends the exact merge command",async()=>{mocked.request.mockReset().mockResolvedValueOnce([proposal]).mockResolvedValueOnce({}).mockResolvedValueOnce([]);render(<AdminCatalogProposalsPage/>);await userEvent.click(await screen.findByRole("button",{name:"Объединить"}));const dialog=screen.getByRole("dialog");await userEvent.selectOptions(within(dialog).getByLabelText("Активное значение"),"canonical-1");const submit=within(dialog).getByRole("button",{name:"Объединить"});expect((submit as HTMLButtonElement).disabled).toBe(true);await userEvent.type(within(dialog).getByLabelText("Причина"),"Дубликат");await userEvent.click(submit);await waitFor(()=>expect(mocked.request).toHaveBeenCalledWith("/api/catalog/proposals/subject/proposal-1/merge",expect.objectContaining({body:JSON.stringify({target_id:"canonical-1",reason:"Дубликат"})})));expect(await screen.findByText("Новых предложений нет.")).toBeTruthy()});

 it("requires a reject reason and removes the row only after success",async()=>{mocked.request.mockReset().mockResolvedValueOnce([proposal]).mockResolvedValueOnce({}).mockResolvedValueOnce([]);render(<AdminCatalogProposalsPage/>);await userEvent.click(await screen.findByRole("button",{name:"Отклонить"}));const dialog=screen.getByRole("dialog"),submit=within(dialog).getByRole("button",{name:"Отклонить"});expect((submit as HTMLButtonElement).disabled).toBe(true);expect(mocked.request).toHaveBeenCalledTimes(1);await userEvent.type(within(dialog).getByLabelText("Причина"),"Некорректно");await userEvent.click(submit);expect(await screen.findByText("Новых предложений нет.")).toBeTruthy();expect(mocked.request).toHaveBeenNthCalledWith(2,"/api/catalog/proposals/subject/proposal-1/reject",expect.objectContaining({body:JSON.stringify({reason:"Некорректно"})}))});

 it.each([
  ["catalog_proposal_in_use","Значение используется"],
  ["catalog_parent_unresolved","Сначала обработайте родительское"],
  ["catalog_merge_hierarchy_mismatch","другой части каталога"],
 ] as const)("retains the proposal after bounded %s",async(code,text)=>{mocked.request.mockReset();mocked.request.mockResolvedValueOnce([proposal]).mockRejectedValueOnce(new api.ApiError(409,code,"conflict"));render(<AdminCatalogProposalsPage/>);await userEvent.click(await screen.findByRole("button",{name:"Отклонить"}));await userEvent.type(screen.getByLabelText("Причина"),"Причина");await userEvent.click(within(screen.getByRole("dialog")).getByRole("button",{name:"Отклонить"}));expect((await screen.findByRole("alert")).textContent).toContain(text);expect(screen.getByText("Алгебра")).toBeTruthy()});

 it("refreshes instead of applying stale success on a concurrent resolution",async()=>{mocked.request.mockReset();mocked.request.mockResolvedValueOnce([proposal]).mockRejectedValueOnce(new api.ApiError(409,"catalog_proposal_already_resolved","conflict")).mockResolvedValueOnce([]);render(<AdminCatalogProposalsPage/>);await userEvent.click(await screen.findByRole("button",{name:"Подтвердить"}));expect((await screen.findByRole("alert")).textContent).toContain("другим администратором");expect(await screen.findByText("Новых предложений нет.")).toBeTruthy();expect(mocked.request).toHaveBeenCalledTimes(3)});
});

describe("catalog.manage capability",()=>{
 it("exposes the route and navigation only with the capability",async()=>{history.replaceState({},"","/admin/catalog-proposals");render(<App principal={principal(["catalog.manage"])}/>);expect(await screen.findByRole("heading",{name:"Предложения каталога"})).toBeTruthy();expect(screen.getByRole("link",{name:"Предложения каталога"})).toBeTruthy()});
 it("denies the route and hides navigation without the capability",()=>{history.replaceState({},"","/admin/catalog-proposals");render(<App principal={principal(["content.read"])}/>);expect(screen.getByRole("heading",{name:"Нет доступа"})).toBeTruthy();expect(screen.queryByRole("link",{name:"Предложения каталога"})).toBeNull()});
});
