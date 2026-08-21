import {afterEach,describe,expect,it,vi} from "vitest";
import {cleanup,render,screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {ReasonDialog,StatusActionBar,ValidationIssueList} from "./StatusActions";

afterEach(()=>cleanup());
const bar=(status:string,extra={})=>render(<StatusActionBar status={status} archived={status==="archived"} dirty={false} busy={null} methodologySaving={false} onAction={()=>{}} {...extra}/>);
describe("status action bar",()=>{
 it("shows only draft actions",()=>{bar("draft");expect(screen.getByRole("button",{name:"Отправить на проверку"})).toBeTruthy();expect(screen.getByRole("button",{name:"Архивировать задание"})).toBeTruthy();expect(screen.queryByRole("button",{name:"Утвердить"})).toBeNull()});
 it("shows only review actions",()=>{bar("review");expect(screen.getByRole("button",{name:"Вернуть в черновик"})).toBeTruthy();expect(screen.getByRole("button",{name:"Утвердить"})).toBeTruthy();expect(screen.queryByRole("button",{name:"Создать новую версию"})).toBeNull()});
 it("keeps approved tasks read-only",()=>{bar("approved");expect(screen.queryByRole("button",{name:"Создать новую версию"})).toBeNull();expect(screen.queryByRole("button",{name:"Отправить на проверку"})).toBeNull()});
 it("has no archived actions",()=>{bar("archived");expect(screen.queryAllByRole("button")).toHaveLength(0);expect(screen.getByText(/только для чтения/)).toBeTruthy()});
 it("blocks every action while dirty",()=>{bar("review",{dirty:true});screen.getAllByRole("button").forEach(x=>expect((x as HTMLButtonElement).disabled).toBe(true));expect(screen.getByText("Сначала сохраните или отмените изменения методической структуры")).toBeTruthy()});
 it("prevents double action while busy",()=>{bar("draft",{busy:"submit"});expect((screen.getByRole("button",{name:"Отправка на проверку…"}) as HTMLButtonElement).disabled).toBe(true);expect((screen.getByRole("button",{name:"Архивировать задание"}) as HTMLButtonElement).disabled).toBe(true)});
});
describe("reason dialogs",()=>{
 it("requires and trims return reason",async()=>{const submit=vi.fn();render(<ReasonDialog mode="return" busy={false} onClose={()=>{}} onSubmit={submit}/>);const button=screen.getByRole("button",{name:"Вернуть в черновик"}) as HTMLButtonElement;expect(button.disabled).toBe(true);await userEvent.type(screen.getByRole("textbox"),"  исправить решение  ");await userEvent.click(button);expect(submit).toHaveBeenCalledWith("исправить решение")});
 it("limits reason to 1000 characters",()=>{render(<ReasonDialog mode="archive" busy={false} onClose={()=>{}} onSubmit={()=>{}}/>);expect(screen.getByRole("textbox").getAttribute("maxlength")).toBe("1000");expect(screen.getByText("0 / 1000")).toBeTruthy()});
 it("allows archive without reason",async()=>{const submit=vi.fn();render(<ReasonDialog mode="archive" busy={false} onClose={()=>{}} onSubmit={submit}/>);await userEvent.click(screen.getByRole("button",{name:"Архивировать задание"}));expect(submit).toHaveBeenCalledWith("")});
});
it("renders every validation issue without raw JSON",()=>{render(<ValidationIssueList title="Методические предупреждения" kind="warning" issues={[{field:"a",code:"one",message:"Первое"},{field:"b",code:"two",message:"Второе"}]}/>);expect(screen.getByText("Первое")).toBeTruthy();expect(screen.getByText("Второе")).toBeTruthy();expect(screen.getByText("Поле: a")).toBeTruthy();expect(screen.getByText("Код: two")).toBeTruthy()});
