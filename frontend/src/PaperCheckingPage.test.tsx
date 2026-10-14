import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {ApiError} from "./api";
import * as scan from "./paperScanApi";
import * as api from "./paperReviewApi";
import {PaperCheckingPage} from "./PaperCheckingPage";
import {effectivePaper, progressFixture, reviewFixture, reviewFinding} from "./paperReviewTestFixtures";
import {batchFixture, pageFixture} from "./paperScanTestFixtures";
import {groupingFixture, groupingPage} from "./paperMatchingTestFixtures";
vi.mock("./paperScanApi"); vi.mock("./paperReviewApi");
let progress: api.CheckingProgress;
let review: api.PaperReview;
const dirty = vi.fn();
const page = () => render(<PaperCheckingPage batchId="b" assignmentId="a" base="/classrooms/c/assignments/a/paper" navigate={vi.fn()} onDirtyChange={dirty}/>);
const ai = (persisted = true) => {progress = progressFixture({batch_status: "checking_completed", not_started: 0, completed_with_review_required: 1, papers: [effectivePaper({checking_state: "completed_with_review_required", effective_check_run_id: "run1", ai_review_available: true, manual_review_available: false, review_id: persisted ? "review1" : null, review_state: persisted ? "reviewing" : null})]});};
const editor = async () => {ai(); page(); await screen.findByLabelText("Итоговый комментарий");};
beforeEach(() => {
  progress = progressFixture(); review = reviewFixture();
  vi.mocked(scan.getScanBatch).mockResolvedValue(batchFixture({status: "ready_for_checking"}));
  vi.mocked(scan.readScanGrouping).mockResolvedValue(groupingFixture({grouping_status: "confirmed", groups: [{assignment_participant_id: "s1", display_name: "Иванов Иван", student_id: "private", assigned_variant_id: "v1", pages: [groupingPage("p1", 0), groupingPage("p2", 1)]}]}));
  vi.mocked(scan.listScanPages).mockResolvedValue({items: [pageFixture({id: "p1"}), pageFixture({id: "p2"})]});
  vi.mocked(scan.readScanPageContent).mockResolvedValue(new Blob(["image"], {type: "image/png"}));
  vi.stubGlobal("URL", Object.assign(URL, {createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn()}));
  vi.mocked(api.readChecking).mockImplementation(async () => structuredClone(progress));
  vi.mocked(api.openReview).mockImplementation(async () => structuredClone(review));
  vi.mocked(api.readReview).mockImplementation(async () => structuredClone(review));
  vi.mocked(api.readAISuggestions).mockResolvedValue({summary_draft: "AI", items: review.draft.items.map(i => ({assessment_item_id: i.assessment_item_id, status: "partially_correct", suggested_score: "1", max_score: "2"}))});
  vi.mocked(api.saveReview).mockImplementation(async (_, version, draft) => {if (version !== review.version) throw new ApiError(409, "paper_review_version_conflict", ""); review = {...review, version: version + 1, state: "reviewing", draft: structuredClone(draft)}; return structuredClone(review);});
  vi.mocked(api.approveReview).mockImplementation(async () => {review = {...review, state: "approved", version: review.version + 1, latest_approved_revision_id: "approved1", latest_approved_revision: {id: "approved1", revision_number: review.version, revision_kind: "approved", snapshot: structuredClone(review.draft), grade_result: {calculated_total_score: "2", total_max_score: "4", calculated_percentage: "50", calculated_grade: "3", final_grade: "3", grade_override_reason: null}}}; return structuredClone(review);});
  vi.mocked(api.executeChecking).mockImplementation(async () => ({outcomes: [], progress}));
});
afterEach(() => {cleanup(); vi.resetAllMocks(); vi.restoreAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals();});
it("ready workspace offers high-level start and suppresses duplicate clicks", async () => {
  let done!: (p: api.CheckingProgress) => void;
  vi.mocked(api.startChecking).mockImplementation(() => new Promise(resolve => {done = resolve;}));
  page(); const start = await screen.findByRole("button", {name: "Начать проверку"}); fireEvent.click(start); fireEvent.click(start);
  expect(api.startChecking).toHaveBeenCalledTimes(1);
  await act(async () => done(progress)); await waitFor(() => expect(api.executeChecking).toHaveBeenCalledTimes(1));
});
it("capacity wait remains queued and never creates a retry", async () => {
  progress = progressFixture({not_started: 0, pending: 1, papers: [effectivePaper({checking_state: "pending", can_execute: true, manual_review_available: false, effective_check_run_id: "r"})]});
  vi.mocked(api.executeChecking).mockResolvedValue({outcomes: [{check_run_id: "r", code: "paper_check_capacity_unavailable"}], progress});
  page(); fireEvent.click(await screen.findByRole("button", {name: "Продолжить проверку"}));
  await screen.findByText(/Работы ожидают свободного места/); expect(screen.getByText("Ожидает очереди")).toBeTruthy();
  expect(screen.queryByRole("button", {name: "Повторить проверку"})).toBeNull(); expect(api.retryPaper).not.toHaveBeenCalled();
});
it("reconciles checking lifecycle after bounded execution when permitted", async () => {
  progress = progressFixture({pending: 1, not_started: 0, can_reconcile: true, papers: [effectivePaper({can_execute: true, checking_state: "pending"})]});
  page(); fireEvent.click(await screen.findByRole("button", {name: "Продолжить проверку"})); await waitFor(() => expect(api.reconcileChecking).toHaveBeenCalledWith("b"));
});
it("restores effective run and opens AI review after reload without navigation state", async () => {
  ai(false); const first = page(); fireEvent.click(await screen.findByRole("button", {name: "Открыть проверку"})); await screen.findByLabelText("Итоговый комментарий");
  expect(api.openReview).toHaveBeenCalledWith(expect.objectContaining({effective_check_run_id: "run1"})); first.unmount();
  ai(); page(); await screen.findByLabelText("Итоговый комментарий"); expect(api.readReview).toHaveBeenCalledWith(expect.objectContaining({effective_check_run_id: "run1"}), expect.any(AbortSignal));
});
it("findings join canonical ID rather than array order and select page evidence", async () => {
  review.draft.findings = [reviewFinding({assessment_item_id: "i2"})]; await editor();
  const items = screen.getAllByRole("article"); expect(within(items[0]).queryByRole("button", {name: "Проверьте вычисление"})).toBeNull();
  fireEvent.click(within(items[1]).getByRole("button", {name: "Проверьте вычисление"}));
  expect(screen.getByLabelText("Страница работы")).toHaveProperty("value", "p2");
  const overlay = await screen.findByRole("button", {name: "Замечание: Проверьте вычисление"}); expect(overlay.getAttribute("aria-pressed")).toBe("true"); expect(overlay.style.left).toBe("25%");
});
it("renders all separate supported regions for an item with textual equivalents", async () => {
  review.draft.findings.push(reviewFinding({finding_id: "f2", short_explanation: "Второе замечание"})); await editor();
  fireEvent.click(screen.getByRole("button", {name: "Проверьте вычисление"})); await screen.findByRole("button", {name: "Замечание: Второе замечание"});
});
it("accepted edits preserve immutable suggestions and CAS increments version", async () => {
  await editor(); fireEvent.change(screen.getAllByLabelText("Решение учителя")[0], {target: {value: "correct"}}); fireEvent.change(screen.getAllByLabelText(/Баллы · максимум/)[0], {target: {value: "2"}});
  expect(screen.getAllByText("Предложено: Частично верно · 1 / 2")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", {name: "Сохранить"})); await screen.findByText("Черновик сохранён.");
  expect(api.saveReview).toHaveBeenCalledWith(expect.anything(), 1, expect.objectContaining({items: expect.arrayContaining([expect.objectContaining({status: "correct", accepted_score: "2"})])}));
  fireEvent.click(screen.getByRole("button", {name: "Подтвердить проверку"})); await screen.findByText(/Баллы: 2 \/ 4/);
  expect(api.approveReview).toHaveBeenCalledWith(expect.anything(), 2, null);
});
it("CAS conflict reloads another teacher's edit and does not overwrite", async () => {
  await editor(); fireEvent.change(screen.getByLabelText("Итоговый комментарий"), {target: {value: "Stale B"}});
  review = {...review, version: 2, draft: {...review.draft, summary: "Сохранено учителем A"}};
  fireEvent.click(screen.getByRole("button", {name: "Сохранить"})); await screen.findByText("Проверка была изменена другим учителем. Загружена актуальная версия.");
  expect(screen.getByLabelText("Итоговый комментарий")).toHaveProperty("value", "Сохранено учителем A"); expect(review.version).toBe(2);
});
it("surfaces backend approval validation safely", async () => {
  await editor(); vi.mocked(api.approveReview).mockRejectedValue(new ApiError(422, "paper_review_unresolved_item", "raw AI private"));
  fireEvent.click(screen.getByRole("button", {name: "Подтвердить проверку"})); expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Оцените все задания перед подтверждением.");
});
it("manual skeleton is server-owned, unresolved, has no AI suggestions and saves", async () => {
  review = reviewFixture(true); page(); fireEvent.click(await screen.findByRole("button", {name: "Проверить вручную"})); await screen.findByLabelText("Итоговый комментарий");
  expect(screen.getAllByText(/Не оценено · —/)).toHaveLength(2); expect(api.readAISuggestions).not.toHaveBeenCalled();
  expect(screen.getByRole("button", {name: "Подтвердить проверку"}).hasAttribute("disabled")).toBe(true);
  screen.getAllByLabelText("Решение учителя").forEach(input => fireEvent.change(input, {target: {value: "correct"}}));
  screen.getAllByLabelText(/Баллы · максимум/).forEach(input => fireEvent.change(input, {target: {value: "2"}}));
  fireEvent.change(screen.getByLabelText("Итоговый комментарий"), {target: {value: "Ручная оценка"}});
  fireEvent.click(screen.getByRole("button", {name: "Сохранить"})); await screen.findByText("Черновик сохранён."); expect(review.source_kind).toBe("manual");
});
it.each(["pending", "running", "completed_with_review_required"])("hides manual start when backend forbids %s", async checking_state => {
  progress.papers = [effectivePaper({checking_state, manual_review_available: false})]; page(); await screen.findByText(/Проверено:/); expect(screen.queryByRole("button", {name: "Проверить вручную"})).toBeNull();
});
it("removes AI finding from draft without mutating source", async () => {
  await editor(); fireEvent.click(screen.getByRole("button", {name: "Убрать замечание из проверки"})); fireEvent.click(screen.getByRole("button", {name: "Сохранить"}));
  await waitFor(() => expect(api.saveReview).toHaveBeenCalledWith(expect.anything(), 1, expect.objectContaining({findings: []})));
});
it("teacher finding uses chosen canonical item and explicit page point", async () => {
  await editor(); fireEvent.click(screen.getAllByRole("button", {name: "Добавить замечание"})[1]);
  fireEvent.change(screen.getByLabelText("Текст замечания"), {target: {value: "Учитель добавил"}});
  fireEvent.change(screen.getByLabelText("Координата x"), {target: {value: "0.4"}}); fireEvent.change(screen.getByLabelText("Координата y"), {target: {value: "0.6"}});
  fireEvent.click(screen.getByRole("button", {name: "Добавить", exact: true})); fireEvent.click(screen.getByRole("button", {name: "Сохранить"}));
  await waitFor(() => expect(api.saveReview).toHaveBeenCalledWith(expect.anything(), 1, expect.objectContaining({findings: expect.arrayContaining([expect.objectContaining({assessment_item_id: "i2", source_check_finding_id: null, scan_page_id: "p1", region: expect.objectContaining({x: .4, y: .6})})])})));
});
it("approved view enters deliberate correction and retains old approved result", async () => {
  ai(); review = await api.approveReview(progress.papers[0], 1, null); page(); await screen.findByRole("button", {name: "Изменить проверку"});
  expect(screen.getAllByLabelText("Решение учителя")[0].hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", {name: "Изменить проверку"})); fireEvent.change(screen.getByLabelText("Итоговый комментарий"), {target: {value: "Исправленная проверка"}});
  fireEvent.click(screen.getByRole("button", {name: "Сохранить"})); await screen.findByText("Новый черновик не подтверждён. Предыдущая подтверждённая версия сохранена.");
  expect(screen.getByText(/Баллы: 2 \/ 4/)).toBeTruthy(); fireEvent.click(screen.getByRole("button", {name: "Подтвердить проверку"})); await screen.findByRole("button", {name: "Изменить проверку"});
});
it.each(["checking_completed", "completed"])("keeps review usable in %s batch", async batch_status => {ai(); progress.batch_status = batch_status; page(); await screen.findByRole("button", {name: "Подтвердить проверку"});});
it("requires reason for optional grade override", async () => {
  await editor(); fireEvent.change(screen.getByLabelText("Итоговая оценка"), {target: {value: "4"}}); expect(screen.getByRole("button", {name: "Подтвердить проверку"}).hasAttribute("disabled")).toBe(true);
  fireEvent.change(screen.getByLabelText("Причина изменения"), {target: {value: "Обоснование"}}); fireEvent.click(screen.getByRole("button", {name: "Подтвердить проверку"})); await waitFor(() => expect(api.approveReview).toHaveBeenCalledWith(expect.anything(), 1, {override_grade: "4", reason: "Обоснование"}));
});
it.each([403, 404, 500])("fails closed on progress denial %s", async status => {vi.mocked(api.readChecking).mockRejectedValue(new ApiError(status, "unknown", "SQL private")); page(); await screen.findByRole("alert"); expect(screen.queryByRole("button", {name: "Начать проверку"})).toBeNull(); expect(document.body.textContent).not.toMatch(/SQL|private/);});
it("rejects foreign assignment before private reads", async () => {vi.mocked(scan.getScanBatch).mockResolvedValue(batchFixture({assignment_id: "foreign"})); page(); await screen.findByRole("alert"); expect(api.readChecking).not.toHaveBeenCalled();});
it("loads selected page only and keeps selected paper during progress refresh", async () => {
  page(); await screen.findByText("Не проверялась"); await waitFor(() => expect(scan.readScanPageContent).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", {name: "Обновить данные"})); await waitFor(() => expect(api.readChecking).toHaveBeenCalledTimes(2)); expect(scan.readScanPageContent).toHaveBeenCalledTimes(1);
});
it("polls transient work without overlap and stops on stable progress", async () => {
  vi.useFakeTimers(); progress.pending = 1; page(); await act(async () => {}); expect(api.readChecking).toHaveBeenCalledTimes(1);
  await act(async () => {await vi.advanceTimersByTimeAsync(3000);}); expect(api.readChecking).toHaveBeenCalledTimes(2);
  progress.pending = 0; await act(async () => {await vi.advanceTimersByTimeAsync(3000);}); expect(api.readChecking).toHaveBeenCalledTimes(3);
  await act(async () => {await vi.advanceTimersByTimeAsync(9000);}); expect(api.readChecking).toHaveBeenCalledTimes(3);
});
it("dirty paper switch keeps edits when cancelled and uses existing navigation guard", async () => {
  await editor(); progress.papers.push(effectivePaper({paper_submission_id: "paper2", assignment_participant_id: "s2", display_name: "Петрова Анна"}));
  fireEvent.click(screen.getByRole("button", {name: "Обновить данные"})); await screen.findByRole("button", {name: "Петрова Анна Не проверялась"});
  fireEvent.change(screen.getByLabelText("Итоговый комментарий"), {target: {value: "Несохранённое решение"}});
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", {name: "Петрова Анна Не проверялась"}));
  expect(confirm).toHaveBeenCalled(); expect(screen.getByLabelText("Итоговый комментарий")).toHaveProperty("value", "Несохранённое решение"); expect(dirty).toHaveBeenLastCalledWith(true);
});
it("beforeunload prevents silent loss of local edits", async () => {
  await editor(); fireEvent.change(screen.getByLabelText("Итоговый комментарий"), {target: {value: "Несохранено"}});
  const event = new Event("beforeunload", {cancelable: true}); window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true);
});
it("point addition requires both explicit coordinates", async () => {
  await editor(); fireEvent.click(screen.getAllByRole("button", {name: "Добавить замечание"})[0]); fireEvent.change(screen.getByLabelText("Текст замечания"), {target: {value: "Учитель"}});
  fireEvent.change(screen.getByLabelText("Координата x"), {target: {value: ".5"}}); expect(screen.getByRole("button", {name: "Добавить", exact: true}).hasAttribute("disabled")).toBe(true);
  fireEvent.change(screen.getByLabelText("Координата y"), {target: {value: ".5"}}); expect(screen.getByRole("button", {name: "Добавить", exact: true}).hasAttribute("disabled")).toBe(false);
});
it("edits a teacher-owned finding and moves its evidence with the same identity", async () => {
  review.draft.findings = [reviewFinding({source_check_finding_id: null})]; await editor();
  fireEvent.change(screen.getByLabelText("Текст замечания"), {target: {value: "Исправленное замечание"}});
  fireEvent.change(screen.getByLabelText("Важность замечания"), {target: {value: "warning"}});
  fireEvent.click(screen.getByRole("button", {name: "Изменить место замечания"}));
  fireEvent.change(screen.getByLabelText("Координата x"), {target: {value: ".6"}}); fireEvent.change(screen.getByLabelText("Координата y"), {target: {value: ".4"}});
  fireEvent.click(screen.getByRole("button", {name: "Применить место"})); fireEvent.click(screen.getByRole("button", {name: "Сохранить"}));
  await waitFor(() => expect(api.saveReview).toHaveBeenCalledWith(expect.anything(), 1, expect.objectContaining({findings: [expect.objectContaining({finding_id: "f1", short_explanation: "Исправленное замечание", severity: "warning", region: expect.objectContaining({x: .6, y: .4})})]})));
});
it("manual lineage hides AI actions even if historical result exists", async () => {
  progress.papers = [effectivePaper({checking_state: "manual_reviewing", manual_review_available: true, review_id: "r", review_state: "reviewing"})]; review = reviewFixture(true);
  page(); await screen.findByLabelText("Итоговый комментарий"); expect(api.readAISuggestions).not.toHaveBeenCalled(); expect(screen.queryByRole("button", {name: "Повторить проверку"})).toBeNull(); expect(api.startChecking).not.toHaveBeenCalled();
});
it("legacy unassociated finding stays visible and blocks approval until linked", async () => {
  review.draft.findings = [reviewFinding({assessment_item_id: null, source_check_finding_id: null})]; await editor();
  expect(screen.getByRole("heading", {name: "Замечания без связи с заданием"})).toBeTruthy(); expect(screen.getByRole("button", {name: "Подтвердить проверку"}).hasAttribute("disabled")).toBe(true);
  fireEvent.change(screen.getByLabelText("Задание", {exact: true}), {target: {value: "i1"}}); fireEvent.click(screen.getByRole("button", {name: "Сохранить"})); await screen.findByText("Черновик сохранён."); expect(screen.queryByRole("heading", {name: "Замечания без связи с заданием"})).toBeNull();
});
it("coincident overlays defer to the textual finding list instead of guessing", async () => {
  review.draft.findings.push(reviewFinding({finding_id: "f2", short_explanation: "Другое замечание"})); await editor();
  fireEvent.click(screen.getByRole("button", {name: "Проверьте вычисление"})); fireEvent.click(await screen.findByRole("button", {name: "Замечание: Другое замечание"}));
  expect(screen.getByText("В этом месте несколько замечаний. Выберите нужное в списке заданий.")).toBeTruthy();
  expect(screen.getByRole("button", {name: "Замечание: Проверьте вычисление"}).getAttribute("aria-pressed")).toBe("true");
});
it("pending effective run becoming reviewable loads immutable suggestions without remount", async () => {
  progress = progressFixture({pending: 1, not_started: 0, papers: [effectivePaper({checking_state: "pending", effective_check_run_id: "run1", manual_review_available: false})]});
  page(); await screen.findByText("Ожидает очереди"); expect(api.readAISuggestions).not.toHaveBeenCalled();
  ai(false); fireEvent.click(screen.getByRole("button", {name: "Обновить данные"})); fireEvent.click(await screen.findByRole("button", {name: "Открыть проверку"}));
  expect(await screen.findAllByText("Предложено: Частично верно · 1 / 2")).toHaveLength(2);
});
it("discovers a shared review opened by another teacher while workspace remains mounted", async () => {
  ai(false); page(); await screen.findByRole("button", {name: "Открыть проверку"});
  progress.papers[0] = {...progress.papers[0], review_id: "review1", review_state: "reviewing"};
  fireEvent.click(screen.getByRole("button", {name: "Обновить данные"})); await screen.findByLabelText("Итоговый комментарий"); expect(api.openReview).not.toHaveBeenCalled();
});
it("transient progress failure preserves unsaved review while authorization denial clears it", async () => {
  await editor(); fireEvent.change(screen.getByLabelText("Итоговый комментарий"), {target: {value: "Сохранить мои решения"}});
  vi.mocked(api.readChecking).mockRejectedValueOnce(new ApiError(500, "unknown", "SQL secret")); fireEvent.click(screen.getByRole("button", {name: "Обновить данные"}));
  await screen.findByRole("alert"); expect(screen.getByLabelText("Итоговый комментарий")).toHaveProperty("value", "Сохранить мои решения");
  vi.mocked(api.readChecking).mockRejectedValueOnce(new ApiError(403, "forbidden", "")); fireEvent.click(screen.getByRole("button", {name: "Обновить данные"}));
  await waitFor(() => expect(screen.queryByLabelText("Итоговый комментарий")).toBeNull());
});
