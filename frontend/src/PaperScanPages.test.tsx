import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {act, cleanup, fireEvent, render, renderHook, screen, waitFor} from "@testing-library/react";
import {ApiError, getAssessment, getTeacherAssignment} from "./api";
import * as api from "./paperScanApi";
import {PaperScanPage} from "./PaperScanPages";
import {PaperScanIntake} from "./PaperScanIntake";
import {usePaperScanBatch} from "./usePaperScanBatch";
import {artifactFixture, batchFixture, pageFixture} from "./paperScanTestFixtures";
vi.mock("./api", async original => ({...await original<typeof import("./api")>(), getTeacherAssignment: vi.fn(), getAssessment: vi.fn()}));
vi.mock("./paperScanApi", () => ({createScanBatch: vi.fn(), listScanBatches: vi.fn(), getScanBatch: vi.fn(), listScanArtifacts: vi.fn(), listScanPages: vi.fn(), uploadScanArtifact: vi.fn(), extractScanBatch: vi.fn(), downloadScanArtifact: vi.fn()}));
const navigate = vi.fn();
const intake = () => render(<PaperScanIntake batchId="b" assignmentId="a" participantCount={2} base="/classrooms/c/assignments/a/paper" navigate={navigate}/>);
const landing = () => render(<PaperScanPage classId="c" assignmentId="a" navigate={navigate}/>);
const ready = () => {vi.mocked(api.getScanBatch).mockResolvedValue(batchFixture({status: "extracting"})); vi.mocked(api.listScanArtifacts).mockResolvedValue({items: [artifactFixture({extraction_status: "completed", page_count: 1})]}); vi.mocked(api.listScanPages).mockResolvedValue({items: [pageFixture()]});};
beforeEach(() => {
  vi.mocked(getTeacherAssignment).mockResolvedValue({class_group_id: "c", assessment_id: "assessment", participant_count: 2} as Awaited<ReturnType<typeof getTeacherAssignment>>);
  vi.mocked(getAssessment).mockResolvedValue({title: "Контрольная работа"} as Awaited<ReturnType<typeof getAssessment>>);
  vi.mocked(api.listScanBatches).mockResolvedValue({items: [batchFixture(), batchFixture({id: "older", status: "completed"})]});
  vi.mocked(api.getScanBatch).mockResolvedValue(batchFixture());
  vi.mocked(api.listScanArtifacts).mockResolvedValue({items: []}); vi.mocked(api.listScanPages).mockResolvedValue({items: []});
});
afterEach(() => {cleanup(); vi.resetAllMocks(); vi.restoreAllMocks(); vi.useRealTimers();});
it("opens existing and historical batches using route identity", async () => {
  landing(); const links = await screen.findAllByRole("link", {name: /Партия от/}); expect(links).toHaveLength(2);
  fireEvent.click(links[1]); expect(navigate).toHaveBeenCalledWith("/classrooms/c/assignments/a/paper/older");
});
it("creates from server response, protects duplicate clicks and reuses an uncertain command key", async () => {
  let reject!: (e: unknown) => void;
  vi.mocked(api.createScanBatch).mockImplementationOnce(() => new Promise((_, no) => {reject = no;})).mockResolvedValueOnce(batchFixture({id: "server-id"}));
  landing(); fireEvent.change(await screen.findByLabelText("Инструкция к бумажным работам"), {target: {value: "Вариант на листе"}});
  const button = screen.getByRole("button", {name: "Создать партию"}); fireEvent.click(button); fireEvent.click(button);
  expect(api.createScanBatch).toHaveBeenCalledTimes(1);
  await act(async () => reject(new ApiError(500, "oops", "private path")));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("Ошибка сервера"));
  fireEvent.click(screen.getByRole("button", {name: "Создать партию"}));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/classrooms/c/assignments/a/paper/server-id"));
  expect(vi.mocked(api.createScanBatch).mock.calls[0][2]).toBe(vi.mocked(api.createScanBatch).mock.calls[1][2]);
});
it("uploads the entire selection sequentially without truncation or fake percentages", async () => {
  vi.mocked(api.uploadScanArtifact).mockResolvedValue(artifactFixture()); intake();
  const picker = await screen.findByLabelText("Файлы работ");
  const files = Array.from({length: 12}, (_, i) => new File(["%PDF-"], `work-${i}.pdf`, {type: "application/pdf"}));
  fireEvent.change(picker, {target: {files}}); fireEvent.click(screen.getByRole("button", {name: "Загрузить выбранные файлы"}));
  await waitFor(() => expect(api.uploadScanArtifact).toHaveBeenCalledTimes(12));
  expect(vi.mocked(api.uploadScanArtifact).mock.calls.map(x => x[1])).toEqual(files);
  expect(vi.mocked(api.uploadScanArtifact).mock.calls.map(x => x[2])).toEqual(Array.from({length: 12}, (_, i) => i));
  expect(screen.queryByText(/\d+%/)).toBeNull();
});
it("shows rejected and unattempted files, preserves successful server artifacts and never auto-retries upload", async () => {
  vi.mocked(api.uploadScanArtifact).mockResolvedValueOnce(artifactFixture()).mockRejectedValueOnce(new ApiError(413, "scan_artifact_too_large", "private"));
  intake(); fireEvent.change(await screen.findByLabelText("Файлы работ"), {target: {files: ["ok", "large", "remaining"].map(x => new File(["pdf"], `${x}.pdf`, {type: "application/pdf"}))}});
  vi.mocked(api.listScanArtifacts).mockResolvedValue({items: [artifactFixture({original_filename: "ok.pdf"})]});
  fireEvent.click(screen.getByRole("button", {name: "Загрузить выбранные файлы"}));
  await screen.findByText("Файл слишком большой."); expect(screen.getByText(/Не отправлен/)).toBeTruthy(); expect(api.uploadScanArtifact).toHaveBeenCalledTimes(2);
  await screen.findByRole("button", {name: "Скачать ok.pdf"}); expect(screen.getByRole("button", {name: "Начать обработку"}).hasAttribute("disabled")).toBe(true);
});
it("represents partial extraction, permits safe resume and keeps handoff disabled", async () => {
  vi.mocked(api.getScanBatch).mockResolvedValue(batchFixture({status: "extracting"}));
  vi.mocked(api.listScanArtifacts).mockResolvedValue({items: [artifactFixture({extraction_status: "completed", page_count: 1}), artifactFixture({id: "bad", original_filename: "failed.pdf", upload_position: 1, extraction_status: "failed_retryable", failure_code: "page_render_failed"})]});
  vi.mocked(api.listScanPages).mockResolvedValue({items: [pageFixture()]});
  vi.mocked(api.extractScanBatch).mockResolvedValue({batch_id: "b", status: "extracting", artifacts: {completed: 1, failed_retryable: 1, failed_terminal: 0}, pages: {ready: 1}});
  intake(); await screen.findByText("failed.pdf"); expect(screen.getByText("Обработан · Страниц: 1")).toBeTruthy();
  expect(screen.getByRole("button", {name: "Перейти к сопоставлению"}).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", {name: "Продолжить обработку"})); await waitFor(() => expect(api.extractScanBatch).toHaveBeenCalledWith("b"));
});
it("has no fake retry for terminal failure or already progressed batches", async () => {
  vi.mocked(api.getScanBatch).mockResolvedValue(batchFixture({status: "extracting"})); vi.mocked(api.listScanArtifacts).mockResolvedValue({items: [artifactFixture({extraction_status: "failed_terminal", failure_code: "invalid_pdf"})]});
  intake(); await screen.findByText("Не удалось прочитать PDF."); expect(screen.queryByRole("button", {name: "Продолжить обработку"})).toBeNull(); cleanup();
  vi.mocked(api.getScanBatch).mockResolvedValue(batchFixture({status: "completed"})); intake(); await screen.findByText("Партия уже перешла к следующему этапу. Файлы доступны только для просмотра.");
  expect(screen.queryByLabelText("Файлы работ")).toBeNull(); expect(screen.getByRole("button", {name: "О сопоставлении"}).hasAttribute("disabled")).toBe(false);
});
it("reconstructs completion on remount and navigates to the bounded handoff shell", async () => {
  ready(); intake(); await screen.findByRole("heading", {name: "Готово к сопоставлению"}); expect(screen.getByText("Сохранено файлов: 1 · Извлечено страниц: 1")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "Перейти к сопоставлению"})); expect(navigate).toHaveBeenCalledWith("/classrooms/c/assignments/a/paper/b/matching");
  cleanup(); intake(); await screen.findByRole("heading", {name: "Готово к сопоставлению"}); expect(api.getScanBatch).toHaveBeenCalledTimes(2); expect(api.extractScanBatch).not.toHaveBeenCalled();
});
it("shows intentional purged-content feedback and retains metadata", async () => {
  ready(); vi.mocked(api.downloadScanArtifact).mockRejectedValue(new ApiError(410, "scan_artifact_content_purged", "internal")); intake();
  fireEvent.click(await screen.findByRole("button", {name: "Скачать Работа.pdf"})); await screen.findByText("Исходный файл больше не хранится"); expect(screen.getByText("Работа.pdf")).toBeTruthy();
});
it.each([403, 404, 500])("bounds failed read (%s) and clears stale actionable state", async status => {
  ready(); intake(); await screen.findByRole("heading", {name: "Готово к сопоставлению"}); vi.mocked(api.getScanBatch).mockRejectedValue(new ApiError(status, "unknown", "storage path SQL"));
  fireEvent.click(screen.getByRole("button", {name: "Обновить данные"})); await screen.findByRole("alert"); expect(screen.queryByRole("button", {name: "Перейти к сопоставлению"})).toBeNull(); expect(document.body.textContent).not.toContain("SQL");
});
it("polls every three seconds only during extraction, stops at completion, revalidates focus and cleans up", async () => {
  vi.useFakeTimers(); vi.mocked(api.getScanBatch).mockResolvedValue(batchFixture({status: "extracting"})); vi.mocked(api.listScanArtifacts).mockResolvedValue({items: [artifactFixture({extraction_status: "running"})]});
  const view = intake(); await act(async () => {}); expect(screen.getByText(/Обрабатываем файлы/)).toBeTruthy();
  await act(async () => {await vi.advanceTimersByTimeAsync(3000);}); expect(api.getScanBatch).toHaveBeenCalledTimes(2);
  ready(); await act(async () => {await vi.advanceTimersByTimeAsync(3000);}); expect(screen.getByRole("heading", {name: "Готово к сопоставлению"})).toBeTruthy();
  await act(async () => {await vi.advanceTimersByTimeAsync(9000);}); expect(api.getScanBatch).toHaveBeenCalledTimes(3);
  await act(async () => {window.dispatchEvent(new Event("focus"));}); expect(api.getScanBatch).toHaveBeenCalledTimes(4);
  view.unmount(); await act(async () => {await vi.advanceTimersByTimeAsync(6000);}); expect(api.getScanBatch).toHaveBeenCalledTimes(4);
});
it("does not poll waiting uploads and rejects a batch belonging to another assignment", async () => {
  vi.useFakeTimers(); intake(); await act(async () => {}); await act(async () => {await vi.advanceTimersByTimeAsync(9000);}); expect(api.getScanBatch).toHaveBeenCalledTimes(1); cleanup();
  vi.mocked(api.getScanBatch).mockResolvedValue(batchFixture({assignment_id: "foreign"})); intake(); await act(async () => {}); expect(screen.getByRole("alert").textContent).toContain("недоступна"); expect(api.listScanArtifacts).toHaveBeenCalledTimes(1);
});
it("pauses hidden-tab polling and refreshes when visible without overlapping reads", async () => {
  vi.useFakeTimers(); let hidden = false;
  vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
  vi.mocked(api.getScanBatch).mockResolvedValue(batchFixture({status: "extracting"}));
  vi.mocked(api.listScanArtifacts).mockResolvedValue({items: [artifactFixture({extraction_status: "pending"})]});
  intake(); await act(async () => {});
  hidden = true; await act(async () => {document.dispatchEvent(new Event("visibilitychange")); await vi.advanceTimersByTimeAsync(6000);});
  expect(api.getScanBatch).toHaveBeenCalledTimes(1);
  let resolve!: (batch: api.ScanBatch) => void;
  vi.mocked(api.getScanBatch).mockImplementationOnce(() => new Promise(yes => {resolve = yes;}));
  hidden = false; await act(async () => {document.dispatchEvent(new Event("visibilitychange")); window.dispatchEvent(new Event("focus"));});
  expect(api.getScanBatch).toHaveBeenCalledTimes(2);
  await act(async () => {await vi.advanceTimersByTimeAsync(6000);}); expect(api.getScanBatch).toHaveBeenCalledTimes(2);
  await act(async () => resolve(batchFixture({status: "extracting"})));
});
it("refreshes artifact processing while a long extraction command is pending", async () => {
  vi.useFakeTimers();
  const hook = renderHook(({processing}) => usePaperScanBatch("b", "a", processing), {initialProps: {processing: false}});
  await act(async () => {}); expect(api.getScanBatch).toHaveBeenCalledTimes(1);
  vi.mocked(api.getScanBatch).mockResolvedValue(batchFixture({status: "extracting"}));
  vi.mocked(api.listScanArtifacts).mockResolvedValue({items: [artifactFixture({extraction_status: "running"})]});
  hook.rerender({processing: true}); await act(async () => {});
  expect(hook.result.current.data?.artifacts[0].extraction_status).toBe("running");
  ready(); await act(async () => {await vi.advanceTimersByTimeAsync(3000);});
  hook.rerender({processing: false}); await act(async () => {await vi.advanceTimersByTimeAsync(6000);});
  expect(api.getScanBatch).toHaveBeenCalledTimes(3);
});
it("attributes failed pages to their source and never enables handoff for failed page metadata", async () => {
  ready(); vi.mocked(api.listScanPages).mockResolvedValue({items: [pageFixture({status: "failed_terminal", failure_code: "invalid_image", content_url: null})]});
  intake(); await screen.findByText("Не обработано страниц: 1");
  expect(screen.getByRole("button", {name: "Перейти к сопоставлению"}).hasAttribute("disabled")).toBe(true);
  expect(screen.queryByRole("button", {name: "Продолжить обработку"})).toBeNull();
});
