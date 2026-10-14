import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {ApiError, request} from "./api";
import {PaperPublicationPage} from "./PaperPublicationPage";
import type {PublicationPaper, PublicationProgress, PublicationHistory} from "./paperPublicationApi";
import {effectivePaper, progressFixture} from "./paperReviewTestFixtures";
import {publicationState, publishLabel} from "./paperPublicationState";
vi.mock("./api", async original => ({...await original<typeof import("./api")>(), request: vi.fn()}));
const req = vi.mocked(request);
const row = (extra: Partial<PublicationPaper> = {}): PublicationPaper => ({...effectivePaper(), latest_approved_revision_id: "R1", review_state: "approved", active_publication_id: null, active_publication_approved_review_revision_id: null, ...extra});
let data: PublicationProgress;
let history: PublicationHistory[];
let failure: unknown;
let pending: Promise<unknown> | undefined;
let nextId: number;
const navigate = vi.fn();
const page = () => render(<PaperPublicationPage batchId="b" assignmentId="a" base="/paper" navigate={navigate}/>);
const writes = () => req.mock.calls.filter(([, init]) => init?.method === "POST");
const publishButton = () => screen.getByRole("button", {name: /^Опубликовать.*: Иванов/});
const ready = async () => {page(); await screen.findAllByText("Готово к публикации");};
const openWithdraw = async () => {fireEvent.click(await screen.findByRole("button", {name: "Отозвать публикацию: Иванов Иван"})); return screen.getByRole("dialog");};
beforeEach(() => {
  req.mockReset(); navigate.mockReset(); failure = undefined; pending = undefined; nextId = 0; history = [];
  data = {...progressFixture(), batch_status: "checking_completed", active_publication_count: 0, can_mark_checking_completed: true, can_finalize: false, blocking_reasons: ["paper_batch_publications_incomplete"], papers: [row()]};
  req.mockImplementation(async (path, init) => {
    if (path === "/api/assessment-core/scan-batches/b") return {assignment_id: "a"};
    if (path.endsWith("/checking")) return structuredClone(data);
    if (!init?.method) return {items: structuredClone(history)};
    if (pending) await pending;
    if (failure) throw failure;
    if (path.endsWith("/complete")) {data.batch_status = "completed"; data.can_finalize = false; return data;}
    const payload = JSON.parse(String(init.body));
    const entries = payload.entries || [{paper_submission_id: "paper1", publication_id: data.papers[0].active_publication_id, approved_review_revision_id: payload.approved_review_revision_id}];
    for (const entry of entries) {
      const p = data.papers.find(x => x.paper_submission_id === entry.paper_submission_id)!;
      if (path.includes("withdraw")) {
        history = history.map(x => x.id === entry.publication_id ? {...x, is_active: false, active: false, is_withdrawn: true, withdrawal_reason: payload.reason} : x);
        p.active_publication_id = null; p.active_publication_approved_review_revision_id = null;
      } else {
        history = history.map(x => ({...x, active: false, is_active: false}));
        const id = `P${++nextId}`;
        history.push({id, paper_submission_id: p.paper_submission_id, publication_number: nextId, approved_review_revision_id: entry.approved_review_revision_id, published_at: "2026-10-14T12:00:00Z", active: true, is_active: true, is_withdrawn: false, withdrawal_reason: null, withdrawn_at: null, withdrawn_by_user_id: null});
        p.active_publication_id = id; p.active_publication_approved_review_revision_id = entry.approved_review_revision_id;
      }
    }
    data.active_publication_count = data.papers.filter(x => x.active_publication_id).length;
    data.can_finalize = data.batch_status !== "completed" && data.active_publication_count === data.paper_count;
    return {};
  });
});
afterEach(cleanup);

it.each([
  [row(), "ready"], [row({active_publication_id: "A", active_publication_approved_review_revision_id: "R1"}), "published"],
  [row({active_publication_id: "A", active_publication_approved_review_revision_id: "R0"}), "new"],
  [row({latest_approved_revision_id: null, review_state: "reviewing"}), "review"],
  [row({review_state: "reviewing"}), "ready"], [row({active_publication_id: null}), "ready"],
])("derives authoritative projection %j -> %s", (p, state) => expect(publicationState(p as PublicationPaper)).toBe(state));
it("loads one main projection and no history at mount", async () => {await ready(); expect(req.mock.calls.filter(([p]) => p.endsWith("/checking"))).toHaveLength(1); expect(req.mock.calls.some(([p]) => p.endsWith("/publications"))).toBe(false);});
it("publishes exact approved revision and refetches exact active identity", async () => {await ready(); fireEvent.click(publishButton()); await screen.findByText("Опубликовано"); expect(writes()[0][1]).toMatchObject({body: JSON.stringify({approved_review_revision_id: "R1"})}); expect(req.mock.calls.filter(([p]) => p.endsWith("/checking"))).toHaveLength(2);});
it("new approval offers direct replacement without withdrawal", async () => {data.papers = [row({active_publication_id: "A", active_publication_approved_review_revision_id: "R0"})]; page(); fireEvent.click(await screen.findByRole("button", {name: "Опубликовать новую версию: Иванов Иван"})); await screen.findByText("Опубликовано"); expect(writes()).toHaveLength(1); expect(writes()[0][0]).toBe("/api/assessment-core/paper-submissions/paper1/publications");});
it("double click shares one in-flight command", async () => {let release!: () => void; pending = new Promise<void>(r => release = r); await ready(); const b = publishButton(); fireEvent.click(b); fireEvent.click(b); expect(writes()).toHaveLength(1); release(); await screen.findByText("Опубликовано");});
it("transport retry retains original key and payload despite projection advancing", async () => {failure = new TypeError("network"); await ready(); fireEvent.click(publishButton()); const retryButton = await screen.findByRole("button", {name: "Повторить команду"}); const original = writes()[0][1]; data.papers[0].latest_approved_revision_id = "R2"; failure = undefined; fireEvent.click(retryButton); await screen.findByText("Опубликована предыдущая версия"); expect(writes()[1][1]).toEqual(original);});
it.each(["paper_publication_pages_unavailable", "paper_publication_request_key_conflict", "paper_publication_revision_mismatch"])("bounded %s refetches without silently retrying", async code => {failure = new ApiError(409, code, "unsafe detail"); await ready(); fireEvent.click(publishButton()); await screen.findByRole("alert"); await waitFor(() => expect(req.mock.calls.filter(([p]) => p.endsWith("/checking"))).toHaveLength(2)); expect(writes()).toHaveLength(1); expect(screen.queryByRole("button", {name: "Повторить команду"})).toBeNull(); expect(screen.getByRole("alert").textContent).not.toContain("unsafe detail");});
it("history loads only selected paper and exposes teacher audit without snapshot", async () => {history = [{id: "A", paper_submission_id: "paper1", publication_number: 1, approved_review_revision_id: "R1", published_at: "2026-10-14T12:00:00Z", active: false, is_active: false, is_withdrawn: true, withdrawal_reason: "Ошибка оценки", withdrawn_at: "2026-10-14T13:00:00Z", withdrawn_by_user_id: "teacher"}]; await ready(); fireEvent.click(screen.getByRole("button", {name: "История публикаций: Иванов Иван"})); expect(await screen.findByText("Причина отзыва: Ошибка оценки")).toBeTruthy(); fireEvent.click(screen.getByRole("button", {name: "Закрыть"})); expect(publishButton().textContent).toBe("Опубликовать снова"); expect(req.mock.calls.filter(([p]) => p.endsWith("/publications"))).toHaveLength(1);});
it("withdraws exact captured ID with trimmed mandatory reason", async () => {data.papers = [row({active_publication_id: "A", active_publication_approved_review_revision_id: "R1"})]; page(); const dialog = await openWithdraw(); const confirm = within(dialog).getByRole("button", {name: "Подтвердить отзыв"}); expect((confirm as HTMLButtonElement).disabled).toBe(true); fireEvent.change(screen.getByLabelText("Причина отзыва"), {target: {value: "  Исправление  "}}); fireEvent.click(confirm); await screen.findByText("Готово к публикации"); expect(writes()[0]).toMatchObject(["/api/assessment-core/paper-submissions/paper1/publications/A/withdraw", {body: JSON.stringify({reason: "Исправление"})}]);});
it("rejects whitespace and overlong withdrawal reason", async () => {data.papers = [row({active_publication_id: "A"})]; page(); await openWithdraw(); for (const value of ["   ", "x".repeat(501)]) {fireEvent.change(screen.getByLabelText("Причина отзыва"), {target: {value}}); expect((screen.getByRole("button", {name: "Подтвердить отзыв"}) as HTMLButtonElement).disabled).toBe(true);} expect(writes()).toHaveLength(0);});
it("stale withdrawal refetches B and never substitutes B into A's command", async () => {data.papers = [row({active_publication_id: "A"})]; page(); await openWithdraw(); data.papers[0].active_publication_id = "B"; failure = new ApiError(409, "paper_publication_not_active", ""); fireEvent.change(screen.getByLabelText("Причина отзыва"), {target: {value: "Ошибка"}}); fireEvent.click(screen.getByRole("button", {name: "Подтвердить отзыв"})); await screen.findByRole("alert"); expect(writes()).toHaveLength(1); expect(writes()[0][0]).toContain("/A/withdraw"); expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Публикация изменилась. Загружены актуальные данные.");});
it("same revision republish creates B with new command key and preserves withdrawn A", async () => {await ready(); fireEvent.click(publishButton()); await screen.findByText("Опубликовано"); await openWithdraw(); fireEvent.change(screen.getByLabelText("Причина отзыва"), {target: {value: "Ошибка"}}); fireEvent.click(screen.getByRole("button", {name: "Подтвердить отзыв"})); await screen.findByText("Готово к публикации"); fireEvent.click(publishButton()); await screen.findByText("Опубликовано"); expect(history.map(x => [x.id, x.is_withdrawn, x.is_active])).toEqual([["P1", true, false], ["P2", false, true]]); expect(writes()[0][1]?.headers).not.toEqual(writes()[2][1]?.headers);});
it("bulk publish uses one selected command with exact revision targets", async () => {data.papers.push(row({paper_submission_id: "p2", display_name: "Петров", latest_approved_revision_id: "R2"})); data.paper_count = 2; await ready(); fireEvent.click(screen.getByLabelText("Выбрать доступные работы")); fireEvent.click(screen.getByRole("button", {name: "Опубликовать выбранные (2)"})); await screen.findByText("Опубликовано 2 из 2"); expect(writes()).toHaveLength(1); expect(writes()[0][0]).toContain("/publications/selected"); expect(JSON.parse(String(writes()[0][1]?.body)).entries).toEqual([{paper_submission_id: "paper1", approved_review_revision_id: "R1"}, {paper_submission_id: "p2", approved_review_revision_id: "R2"}]); expect(screen.getByText("Выбрано: 0")).toBeTruthy();});
it("all-or-none failure leaves server state and clears selection", async () => {failure = new ApiError(409, "paper_publication_batch_has_ineligible_papers", ""); await ready(); fireEvent.click(screen.getByLabelText("Выбрать доступные работы")); fireEvent.click(screen.getByRole("button", {name: "Опубликовать выбранные (1)"})); await screen.findByRole("alert"); expect(data.active_publication_count).toBe(0); expect(screen.getByText("Выбрано: 0")).toBeTruthy();});
it("bulk withdrawal uses selected route and freezes exact identities", async () => {data.papers = [row({active_publication_id: "A"}), row({paper_submission_id: "p2", display_name: "Петров", active_publication_id: "B"})]; data.paper_count = 2; page(); await screen.findByText("Иванов Иван"); fireEvent.click(screen.getByLabelText("Выбрать доступные работы")); fireEvent.click(screen.getByRole("button", {name: "Отозвать выбранные (2)"})); fireEvent.change(screen.getByLabelText("Причина отзыва"), {target: {value: " Исправить "}}); fireEvent.click(screen.getByRole("button", {name: "Подтвердить отзыв"})); await waitFor(() => expect(data.papers.every(x => !x.active_publication_id)).toBe(true)); expect(writes()[0][0]).toContain("/publications/withdraw/selected"); expect(JSON.parse(String(writes()[0][1]?.body))).toEqual({entries: [{paper_submission_id: "paper1", publication_id: "A"}, {paper_submission_id: "p2", publication_id: "B"}], reason: "Исправить"});});
it("unapproved-only paper is never selectable or publishable", async () => {data.papers = [row({latest_approved_revision_id: null, review_state: "reviewing"})]; page(); await screen.findByText("Нет подтверждённой проверки"); expect((screen.getByLabelText("Выбрать работу: Иванов Иван") as HTMLInputElement).disabled).toBe(true); expect(screen.queryByRole("button", {name: /^Опубликовать.*: Иванов/})).toBeNull();});
it("server can_finalize governs readiness and explicit confirmation", async () => {await ready(); expect((screen.getByRole("button", {name: "Завершить партию"}) as HTMLButtonElement).disabled).toBe(true); fireEvent.click(publishButton()); await screen.findByText("Опубликовано"); expect(writes()).toHaveLength(1); fireEvent.click(screen.getByRole("button", {name: "Завершить партию"})); expect(screen.getByRole("dialog").textContent).toContain("можно будет корректировать"); expect(writes()).toHaveLength(1); fireEvent.click(screen.getByRole("button", {name: "Подтвердить завершение"})); await screen.findByText("Партия завершена"); expect(writes()[1][0]).toContain("/complete");});
it("completed survives withdrawal, republish and reload", async () => {data.batch_status = "completed"; data.papers = [row({active_publication_id: "A"})]; page(); await screen.findByText("Партия завершена"); await openWithdraw(); fireEvent.change(screen.getByLabelText("Причина отзыва"), {target: {value: "Ошибка"}}); fireEvent.click(screen.getByRole("button", {name: "Подтвердить отзыв"})); await screen.findByText("Готово к публикации"); expect(screen.getByText("Партия завершена")).toBeTruthy(); fireEvent.click(publishButton()); await screen.findByText("Опубликовано"); cleanup(); page(); await screen.findByText("Партия завершена"); expect(screen.getByText("Опубликовано")).toBeTruthy(); expect(writes()).toHaveLength(2);});
it.each([403, 404])("authorization %s does not render papers", async status => {req.mockRejectedValue(new ApiError(status, "forbidden", "secret")); page(); await screen.findByRole("alert"); expect(screen.queryByText("Иванов Иван")).toBeNull(); expect(writes()).toHaveLength(0);});
it("batch substitution refuses projection fetch", async () => {req.mockResolvedValue({assignment_id: "foreign"}); page(); await screen.findByRole("alert"); expect(req).toHaveBeenCalledTimes(1);});
it("opens contextual PR3 review route", async () => {await ready(); fireEvent.click(screen.getAllByRole("button", {name: "Открыть проверку"})[1]); expect(navigate).toHaveBeenCalledWith("/paper/b/checking?paper=paper1");});
it("simple search and filter work for thirty papers without history fanout", async () => {data.papers = Array.from({length: 30}, (_, i) => row({paper_submission_id: `p${i}`, display_name: `Ученик ${i}`})); data.paper_count = 30; page(); await screen.findByText("Ученик 29"); fireEvent.change(screen.getByLabelText("Поиск ученика"), {target: {value: "Ученик 29"}}); expect(screen.queryByText("Ученик 28")).toBeNull(); fireEvent.change(screen.getByLabelText("Состояние"), {target: {value: "published"}}); expect(screen.getByText("Работ по этому фильтру нет.")).toBeTruthy(); expect(req).toHaveBeenCalledTimes(2);});
it("loaded withdrawal history only changes the action label", () => {expect(publishLabel(row(), [{is_withdrawn: true} as PublicationHistory])).toBe("Опубликовать снова");});
it("one selected withdrawal still uses the official selected endpoint", async () => {
  data.papers = [row({active_publication_id: "A"})]; page(); await screen.findByText("Иванов Иван");
  fireEvent.click(screen.getByLabelText("Выбрать доступные работы")); fireEvent.click(screen.getByRole("button", {name: "Отозвать выбранные (1)"}));
  fireEvent.change(screen.getByLabelText("Причина отзыва"), {target: {value: "Ошибка"}}); fireEvent.click(screen.getByRole("button", {name: "Подтвердить отзыв"}));
  await screen.findByText("Готово к публикации"); expect(writes()[0][0]).toContain("/withdraw/selected");
});
it("bulk withdrawal rejection never retargets advanced publication identities", async () => {
  data.papers = [row({active_publication_id: "A"}), row({paper_submission_id: "p2", display_name: "Петров", active_publication_id: "B"})]; page(); await screen.findByText("Иванов Иван");
  fireEvent.click(screen.getByLabelText("Выбрать доступные работы")); fireEvent.click(screen.getByRole("button", {name: "Отозвать выбранные (2)"}));
  data.papers[0].active_publication_id = "C"; failure = new ApiError(409, "paper_publication_not_active", "");
  fireEvent.change(screen.getByLabelText("Причина отзыва"), {target: {value: "Ошибка"}}); fireEvent.click(screen.getByRole("button", {name: "Подтвердить отзыв"}));
  await screen.findByRole("alert"); expect(writes()).toHaveLength(1); expect(JSON.parse(String(writes()[0][1]?.body)).entries[0].publication_id).toBe("A");
});
it("server readiness can remain false despite every paper having active publication", async () => {
  data.papers = [row({active_publication_id: "A", active_publication_approved_review_revision_id: "R1"})]; data.active_publication_count = 1; page(); await screen.findByText("Опубликовано 1 из 1");
  expect((screen.getByRole("button", {name: "Завершить партию"}) as HTMLButtonElement).disabled).toBe(true);
});
it("completion rejection refetches without fabricating completed state", async () => {
  data.papers = [row({active_publication_id: "A"})]; data.can_finalize = true; page(); await screen.findByText("Иванов Иван");
  fireEvent.click(screen.getByRole("button", {name: "Завершить партию"})); data.can_finalize = false; failure = new ApiError(409, "paper_batch_publications_incomplete", "");
  fireEvent.click(screen.getByRole("button", {name: "Подтвердить завершение"})); await waitFor(() => expect(req.mock.calls.filter(([p]) => p.endsWith("/checking"))).toHaveLength(2));
  expect(screen.queryByText("Партия завершена")).toBeNull(); expect(writes()).toHaveLength(1);
});
it("withdrawal modal traps keyboard focus and restores opener", async () => {
  data.papers = [row({active_publication_id: "A"})]; page(); const opener = await screen.findByRole("button", {name: "Отозвать публикацию: Иванов Иван"}); opener.focus(); fireEvent.click(opener);
  const textarea = screen.getByLabelText("Причина отзыва"); expect(document.activeElement).toBe(textarea);
  fireEvent.keyDown(textarea, {key: "Tab", shiftKey: true}); expect(document.activeElement).toBe(screen.getByRole("button", {name: "Отмена"}));
  fireEvent.keyDown(document.activeElement!, {key: "Escape"}); expect(screen.queryByRole("dialog")).toBeNull(); expect(document.activeElement).toBe(opener);
});
it.each(["ready", "published", "new", "review"])("reload reconstructs %s from server without mutations", async state => {
  data.papers = [row({latest_approved_revision_id: state === "review" ? null : "R1", active_publication_id: ["published", "new"].includes(state) ? "A" : null, active_publication_approved_review_revision_id: state === "new" ? "R0" : "R1"})];
  page(); await screen.findByText("Иванов Иван"); cleanup(); page(); await screen.findByText("Иванов Иван"); expect(writes()).toHaveLength(0);
  expect(publicationState(data.papers[0])).toBe(state);
});
