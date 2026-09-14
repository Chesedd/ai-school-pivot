import {cleanup, render, screen, waitFor, within} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {AdminUsersPage} from "./AdminUsersPage";
import * as adminApi from "./adminApi";

vi.mock("./adminApi", async importOriginal => ({...(await importOriginal<typeof adminApi>()), listUsers: vi.fn(), createUser: vi.fn(), patchUser: vi.fn(), replaceRoles: vi.fn(), resetPassword: vi.fn()}));
const auth = {principal: {user_id: "admin"}, becomeAnonymous: vi.fn(), refreshPrincipal: vi.fn()};
vi.mock("./auth", () => ({useAuth: () => auth}));
const api = vi.mocked(adminApi);
const user = (overrides = {}) => ({user_id: "u1", login: "иван.иванов", display_name: "Иван Иванов", first_name: "Иван", last_name: "Иванов", is_active: true, roles: ["student"] as adminApi.Role[], student_id: null, created_at: "1", updated_at: "1", ...overrides});

describe("AdminUsersPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listUsers.mockResolvedValue({items: [user()], total: 1, offset: 0, limit: 50});
    api.patchUser.mockResolvedValue(user()); api.replaceRoles.mockResolvedValue(user()); api.resetPassword.mockResolvedValue();
    Object.defineProperty(navigator, "clipboard", {configurable: true, value: {writeText: vi.fn().mockResolvedValue(undefined)}});
  });
  afterEach(cleanup);

  it("removes technical student-link controls and renders legacy names safely", async () => {
    api.listUsers.mockResolvedValue({items: [user({user_id: "legacy", login: "legacy", display_name: "Старый пользователь", first_name: null, last_name: null})], total: 1, offset: 0, limit: 50});
    render(<AdminUsersPage/>);
    expect(await screen.findByRole("button", {name: "Старый пользователь"})).toBeTruthy();
    expect(screen.queryByText("Административный идентификатор ученика")).toBeNull();
    expect(screen.queryByText("Профиль ученика")).toBeNull();
    expect(document.body.textContent).not.toContain("student_id");
  });

  it("uses generated mode by default and keeps returned credentials only until dismissal", async () => {
    api.createUser.mockResolvedValue(user({user_id: "u2", generated_password: "Exact-Secret-42"}));
    render(<AdminUsersPage/>); await screen.findByText("Список пользователей");
    await userEvent.click(screen.getByText("Создать пользователя"));
    const form = screen.getByText("Создать пользователя").parentElement!;
    await userEvent.type(within(form).getByLabelText("Имя"), "Иван"); await userEvent.type(within(form).getByLabelText("Фамилия"), "Иванов");
    await userEvent.click(within(form).getByLabelText("Ученик")); await userEvent.click(within(form).getByRole("button", {name: "Создать"}));
    await waitFor(() => expect(api.createUser).toHaveBeenCalledWith({first_name: "Иван", last_name: "Иванов", roles: ["student"], password: {mode: "generated"}}));
    const panel = await screen.findByLabelText("Данные нового пользователя");
    expect(within(panel).getByText("иван.иванов")).toBeTruthy(); expect(within(panel).getByText("Exact-Secret-42")).toBeTruthy();
    expect(api.listUsers.mock.calls.every(call => call.length === 0)).toBe(true);
    await userEvent.click(within(panel).getByRole("button", {name: "Скопировать логин"})); expect(navigator.clipboard.writeText).toHaveBeenCalledWith("иван.иванов");
    await userEvent.click(within(panel).getByRole("button", {name: "Скопировать пароль"})); expect(navigator.clipboard.writeText).toHaveBeenCalledWith("Exact-Secret-42");
    await userEvent.click(within(panel).getByRole("button", {name: "Закрыть"})); expect(screen.queryByText("Exact-Secret-42")).toBeNull();
  });

  it("retains credentials when clipboard access fails", async () => {
    vi.mocked(navigator.clipboard.writeText).mockRejectedValueOnce(new Error("denied"));
    api.createUser.mockResolvedValue(user({generated_password: "keep-me"})); render(<AdminUsersPage/>); await screen.findByText("Список пользователей"); await userEvent.click(screen.getByText("Создать пользователя"));
    const form = screen.getByText("Создать пользователя").parentElement!; await userEvent.type(within(form).getByLabelText("Имя"), "И"); await userEvent.type(within(form).getByLabelText("Фамилия"), "И"); await userEvent.click(within(form).getByRole("button", {name: "Создать"}));
    const panel = await screen.findByLabelText("Данные нового пользователя"); await userEvent.click(within(panel).getByRole("button", {name: "Скопировать пароль"}));
    expect(await within(panel).findByRole("alert")).toBeTruthy(); expect(within(panel).getByText("keep-me")).toBeTruthy();
  });

  it("shows and sends a manually provided password", async () => {
    api.createUser.mockResolvedValue(user()); render(<AdminUsersPage/>); await screen.findByText("Список пользователей"); await userEvent.click(screen.getByText("Создать пользователя"));
    const form = screen.getByText("Создать пользователя").parentElement!; await userEvent.type(within(form).getByLabelText("Имя"), "Анна"); await userEvent.type(within(form).getByLabelText("Фамилия"), "Петрова"); await userEvent.click(within(form).getByLabelText("Ввести пароль вручную"));
    await userEvent.type(within(form).getByLabelText("Пароль"), "manual-secret"); await userEvent.click(within(form).getByRole("button", {name: "Создать"}));
    await waitFor(() => expect(api.createUser).toHaveBeenCalledWith({first_name: "Анна", last_name: "Петрова", roles: [], password: {mode: "provided", value: "manual-secret"}}));
  });

  it("updates structured names without changing login", async () => {
    render(<AdminUsersPage/>); await userEvent.click(await screen.findByRole("button", {name: "Иван"}));
    const editor = screen.getByRole("heading", {name: "Иван Иванов"}).parentElement!; expect(within(editor).getByText(/иван\.иванов/)).toBeTruthy();
    const first = within(editor).getByLabelText("Имя"); await userEvent.clear(first); await userEvent.type(first, "Пётр"); await userEvent.click(within(editor).getByRole("button", {name: "Сохранить данные"}));
    await waitFor(() => expect(api.patchUser).toHaveBeenCalledWith("u1", {first_name: "Пётр", last_name: "Иванов"}));
    expect(api.patchUser.mock.calls[0][1]).not.toHaveProperty("login");
  });
});
