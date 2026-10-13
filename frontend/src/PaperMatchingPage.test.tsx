import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {ApiError, getAssessment, getTeacherAssignment} from "./api";
import {getAssignmentResults} from "./classroomResultsApi";
import * as api from "./paperScanApi";
import {PaperMatchingPage, groupingComplete} from "./PaperMatchingPage";
import {PaperScanPage} from "./PaperScanPages";
import {ScanPageViewer} from "./ScanPageViewer";
import {artifactFixture, batchFixture, pageFixture} from "./paperScanTestFixtures";
import {groupingFixture, groupingPage, matchingFixture, matchingRoster} from "./paperMatchingTestFixtures";
let onDragEnd: ((event: import("@dnd-kit/core").DragEndEvent) => void) | undefined;
vi.mock("@dnd-kit/core", async original => {
  const actual = await original<typeof import("@dnd-kit/core")>();
  return {...actual, DndContext: (props: import("@dnd-kit/core").DndContextProps) => {onDragEnd = props.onDragEnd; return <actual.DndContext {...props}/>;}};
});
vi.mock("./paperScanApi");
vi.mock("./classroomResultsApi");
vi.mock("./api", async original => ({...await original<typeof import("./api")>(), getAssessment: vi.fn(), getTeacherAssignment: vi.fn()}));
let grouping: api.ScanGrouping | undefined;
let matching: api.ScanMatching | undefined;
let status: string;
const navigate = vi.fn();
const page = (checking = false) => render(<PaperMatchingPage batchId="b" assignmentId="a" base="/classrooms/c/assignments/a/paper" variants={{v1: "А"}} navigate={navigate} checking={checking}/>);
const ready = async () => {page(); await screen.findByRole("button", {name: "Подтвердить распределение"});};
const choose = async (pageNumber: number, participant = "s2") => {
  const strip = screen.getByRole("region", {name: "Страницы партии"});
  fireEvent.click(within(strip).getByRole("button", {name: new RegExp(`^Страница ${pageNumber}:`)}));
  fireEvent.change(screen.getByLabelText("Назначить ученику"), {target: {value: participant}});
  fireEvent.click(screen.getByRole("button", {name: "Назначить", exact: true}));
  await waitFor(() => expect(screen.getByRole("button", {name: "Обновить данные"}).hasAttribute("disabled")).toBe(false));
};
beforeEach(() => {
  grouping = groupingFixture(); matching = matchingFixture(); status = "matching_review_required";
  vi.mocked(api.getScanBatch).mockImplementation(async () => batchFixture({status}));
  vi.mocked(api.listScanArtifacts).mockResolvedValue({items: [artifactFixture({extraction_status: "completed", page_count: 3})]});
  vi.mocked(api.listScanPages).mockResolvedValue({items: [0, 1, 2].map(i => pageFixture({id: `p${i + 1}`, source_page_index: i, content_url: `/api/assessment-core/scan-pages/p${i + 1}/content`}))});
  vi.mocked(api.readScanMatching).mockImplementation(async () => {if (!matching) throw new ApiError(404, "matching_not_found", ""); return structuredClone(matching);});
  vi.mocked(api.readScanGrouping).mockImplementation(async () => {if (!grouping) throw new ApiError(404, "grouping_not_found", ""); return structuredClone(grouping);});
  vi.mocked(getAssignmentResults).mockResolvedValue({items: matchingRoster.map(s => ({assignment_participant_id: s.assignment_participant_id, student_display_name: s.display_name}))} as Awaited<ReturnType<typeof getAssignmentResults>>);
  vi.mocked(api.readScanPageContent).mockResolvedValue(new Blob(["image"], {type: "image/png"}));
  vi.stubGlobal("URL", Object.assign(URL, {createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn()}));
  vi.mocked(api.assignScanPage).mockImplementation(async (_, id, participant, expected) => {
    if (!grouping || expected.row_version !== grouping.row_version) throw new ApiError(409, "grouping_revision_conflict", "private");
    const pg = grouping.groups.flatMap(g => g.pages).concat(grouping.unresolved_pages).find(p => p.page_id === id)!;
    grouping.groups.forEach(g => {g.pages = g.pages.filter(p => p.page_id !== id);});
    grouping.groups = grouping.groups.filter(g => g.pages.length);
    grouping.unresolved_pages = grouping.unresolved_pages.filter(p => p.page_id !== id);
    if (participant) {
      let group = grouping.groups.find(g => g.assignment_participant_id === participant);
      if (!group) {group = {...matchingRoster.find(s => s.assignment_participant_id === participant)!, student_id: "private-student", assigned_variant_id: "v1", pages: []}; grouping.groups.push(group);}
      group.pages.push({...pg, page_order: group.pages.length});
    } else grouping.unresolved_pages.push({...pg, page_order: null, candidate_students: [], reason_code: "teacher_unresolved"});
    grouping.row_version++; return structuredClone(grouping);
  });
  vi.mocked(api.reorderScanPages).mockImplementation(async (_, student, ids) => {
    const g = grouping!.groups.find(g => g.assignment_participant_id === student)!;
    g.pages = ids.map((id, index) => ({...g.pages.find(p => p.page_id === id)!, page_order: index}));
    grouping!.row_version++; return structuredClone(grouping!);
  });
  vi.mocked(api.confirmScanGrouping).mockImplementation(async () => {
    grouping!.grouping_status = "confirmed"; grouping!.batch_status = status = "ready_for_checking"; grouping!.row_version++;
    grouping!.paper_submissions = grouping!.groups.map((g, i) => ({id: `paper${i}`, assignment_participant_id: g.assignment_participant_id, assigned_variant_id: g.assigned_variant_id, status: "ready_for_checking"}));
    return structuredClone(grouping!);
  });
  vi.mocked(getTeacherAssignment).mockResolvedValue({class_group_id: "c", assessment_id: "assessment", participant_count: 3} as Awaited<ReturnType<typeof getTeacherAssignment>>);
  vi.mocked(getAssessment).mockResolvedValue({title: "Алгебра", variants: [{id: "v1", name: "А"}]} as Awaited<ReturnType<typeof getAssessment>>);
});
afterEach(() => {cleanup(); vi.resetAllMocks(); vi.restoreAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals();});
it("opens the real workspace by deep link and reconstructs on remount", async () => {
  render(<PaperScanPage classId="c" assignmentId="a" batchId="b" matching navigate={navigate}/>);
  await screen.findByRole("button", {name: "Подтвердить распределение"}); expect(screen.getByRole("heading", {name: "Алгебра"})).toBeTruthy();
  cleanup(); await ready(); expect(api.readScanGrouping).toHaveBeenCalledTimes(2); expect(api.startScanMatching).not.toHaveBeenCalled();
});
it("shows not-started state and starts only after completed intake", async () => {
  grouping = matching = undefined; status = "extracting";
  vi.mocked(api.startScanMatching).mockImplementation(async () => {matching = matchingFixture(); grouping = groupingFixture(); status = "matching_review_required"; return matching;});
  page(); fireEvent.click(await screen.findByRole("button", {name: "Сопоставить страницы"}));
  await screen.findByRole("button", {name: "Подтвердить распределение"}); expect(api.startScanMatching).toHaveBeenCalledTimes(1);
});
it.each(["uploading", "draft", "extracting"])("blocks incomplete intake in %s", async state => {
  grouping = matching = undefined; status = state; vi.mocked(api.listScanArtifacts).mockResolvedValue({items: [artifactFixture({extraction_status: "running"})]});
  page(); await screen.findByText(/Сначала завершите обработку файлов/); expect(screen.queryByRole("button", {name: "Сопоставить страницы"})).toBeNull(); expect(api.startScanMatching).not.toHaveBeenCalled();
});
it("suppresses duplicate start while polling a durable running command", async () => {
  grouping = matching = undefined; status = "extracting";
  let finish!: () => void;
  vi.mocked(api.startScanMatching).mockImplementation(() => {status = "matching"; matching = matchingFixture({status: "running", proposals: []}); return new Promise(resolve => {finish = () => resolve(matching!);});});
  page(); const start = await screen.findByRole("button", {name: "Сопоставить страницы"}); fireEvent.click(start); fireEvent.click(start);
  await screen.findByRole("heading", {name: "Сопоставляем страницы…"}); expect(api.startScanMatching).toHaveBeenCalledTimes(1);
  matching = matchingFixture(); status = "matching_review_required"; grouping = groupingFixture(); await act(async () => finish()); await screen.findByRole("button", {name: "Подтвердить распределение"});
});
it("reads existing running matching without starting another run", async () => {
  grouping = undefined; status = "matching"; matching = matchingFixture({status: "running", proposals: []});
  page(); await screen.findByRole("heading", {name: "Сопоставляем страницы…"}); expect(api.startScanMatching).not.toHaveBeenCalled();
});
it.each(["matching_review_required", "failed_retryable", "failed_terminal"])("polls running every three seconds and stops at %s", async stable => {
  vi.useFakeTimers(); grouping = undefined; status = "matching"; matching = matchingFixture({status: "running", proposals: []});
  const view = page(); await act(async () => {});
  await act(async () => {await vi.advanceTimersByTimeAsync(3000);}); expect(api.readScanMatching).toHaveBeenCalledTimes(2);
  matching = matchingFixture({status: stable}); if (stable === "matching_review_required") {status = stable; grouping = groupingFixture();}
  await act(async () => {await vi.advanceTimersByTimeAsync(3000);}); expect(api.readScanMatching).toHaveBeenCalledTimes(3);
  await act(async () => {await vi.advanceTimersByTimeAsync(9000);}); expect(api.readScanMatching).toHaveBeenCalledTimes(3);
  view.unmount(); await act(async () => {await vi.advanceTimersByTimeAsync(3000);}); expect(api.readScanMatching).toHaveBeenCalledTimes(3);
});
it("retryable failure offers legal resume without creating a new run identity", async () => {
  grouping = undefined; status = "matching"; matching = matchingFixture({status: "failed_retryable"}); vi.mocked(api.startScanMatching).mockResolvedValue(matching);
  page(); fireEvent.click(await screen.findByRole("button", {name: "Продолжить сопоставление"})); await waitFor(() => expect(api.startScanMatching).toHaveBeenCalledWith("b"));
});
it("terminal failure has no retry", async () => {
  grouping = undefined; status = "matching"; matching = matchingFixture({status: "failed_terminal"}); page(); await screen.findByText("Повторное сопоставление этой партии недоступно.");
  expect(screen.queryByRole("button", {name: "Продолжить сопоставление"})).toBeNull();
});
it("initializes a missing server draft from proposals without confirming", async () => {
  grouping = undefined; vi.mocked(api.initializeScanGrouping).mockImplementation(async () => {grouping = groupingFixture(); return grouping;});
  await ready(); expect(api.initializeScanGrouping).toHaveBeenCalledTimes(1); expect(api.confirmScanGrouping).not.toHaveBeenCalled();
});
it("renders AI suggestion, ambiguity, full roster, and canonical variant read-only", async () => {
  await ready(); expect(screen.getByText("Предложено: Иванов Иван")).toBeTruthy(); expect(screen.getByText("Сидоров Пётр")).toBeTruthy(); expect(screen.getByText("2 стр. · Вариант: А")).toBeTruthy();
  fireEvent.click(within(screen.getByRole("region", {name: "Страницы партии"})).getByRole("button", {name: /^Страница 3:/}));
  expect(screen.getByText(/Нужно проверить: неоднозначное/)).toBeTruthy(); expect(screen.getByRole("heading", {name: "Не распределены · 1"})).toBeTruthy();
  expect(screen.queryByLabelText(/Назначить вариант/)).toBeNull();
});
it("does not display provider text, confidence, opaque tokens, identity IDs", async () => {
  await ready(); const text = document.body.textContent!;
  ["private-provider-text", "0.9", "internal-student-id", "v1", "s1", "Anthropic", "ModelRun"].forEach(value => expect(text).not.toContain(value));
});
it("loads only the selected full image, changes selection accessibly and revokes URLs", async () => {
  await ready(); await screen.findByAltText("Страница 1"); expect(api.readScanPageContent).toHaveBeenCalledTimes(1);
  const button = within(screen.getByRole("region", {name: "Страницы партии"})).getByRole("button", {name: /^Страница 3:/}); fireEvent.click(button);
  await screen.findByAltText("Страница 3"); expect(button.getAttribute("aria-pressed")).toBe("true"); expect(api.readScanPageContent).toHaveBeenLastCalledWith("p3", expect.any(AbortSignal)); expect(api.readScanPageContent).toHaveBeenCalledTimes(2); expect(URL.revokeObjectURL).toHaveBeenCalled();
});
it.each([404, 410, 403, 503])("bounds content failure %s without source PDF fallback", async code => {
  vi.mocked(api.readScanPageContent).mockRejectedValue(new ApiError(code, "scan_page_content_purged", "SQL private"));
  await ready(); await screen.findByText(code === 403 ? "Нет доступа к странице." : code === 503 ? "Не удалось загрузить страницу. Повторите загрузку." : "Страница больше не доступна.");
  expect(api.downloadScanArtifact).not.toHaveBeenCalled(); expect(screen.getByRole("button", {name: "Подтвердить распределение"})).toBeTruthy(); expect(document.body.textContent).not.toContain("SQL");
});
it("handles absent normalized content without requesting a source", async () => {
  render(<ScanPageViewer page={pageFixture({content_url: null})} number={1}/>); await screen.findByText("Страница больше не доступна."); expect(api.readScanPageContent).not.toHaveBeenCalled();
});
it("zooms and resets the selected page", async () => {
  await ready(); const img = await screen.findByAltText("Страница 1"); fireEvent.click(screen.getByRole("button", {name: "Увеличить страницу"})); expect(img.style.width).toBe("125%"); fireEvent.click(screen.getByRole("button", {name: "По размеру"})); expect(img.style.width).toBe("100%");
});
it("assigns unresolved page immediately with server CAS and updates completeness", async () => {
  await ready(); expect(screen.getByRole("button", {name: "Подтвердить распределение"}).hasAttribute("disabled")).toBe(true);
  await choose(3); expect(api.assignScanPage).toHaveBeenCalledWith("b", "p3", "s2", expect.objectContaining({row_version: 1, grouping_revision: 1}));
  expect(screen.getByText("3 из 3 страниц распределено")).toBeTruthy(); expect(screen.getByRole("button", {name: "Подтвердить распределение"}).hasAttribute("disabled")).toBe(false); expect(screen.getByRole("heading", {name: "Не распределены · 0"})).toBeTruthy();
});
it("moves A to B, has one paper per participant and never duplicates page membership", async () => {
  await ready(); await choose(1); await choose(2);
  expect(grouping!.groups).toHaveLength(1); expect(grouping!.groups[0].assignment_participant_id).toBe("s2"); expect(grouping!.groups[0].pages.map(p => p.page_id)).toEqual(["p1", "p2"]);
  expect(screen.getByRole("region", {name: "Страницы партии"}).querySelectorAll("button")).toHaveLength(3);
});
it("removes a page back to unassigned without choosing another student", async () => {
  await ready(); fireEvent.click(screen.getByRole("button", {name: "В нераспределённые"}));
  await waitFor(() => expect(screen.getByRole("heading", {name: "Не распределены · 2"})).toBeTruthy()); expect(api.assignScanPage).toHaveBeenCalledWith("b", "p1", null, expect.objectContaining({row_version: 1}));
});
it("reorders by accessible controls with explicit full order and current CAS", async () => {
  await ready(); fireEvent.click(screen.getByRole("button", {name: "Страница 2: вверх"})); await waitFor(() => expect(grouping!.row_version).toBe(2));
  await waitFor(() => expect(screen.getByRole("button", {name: "Обновить данные"}).hasAttribute("disabled")).toBe(false));
  expect(api.reorderScanPages).toHaveBeenCalledWith("b", "s1", ["p2", "p1"], expect.objectContaining({row_version: 1})); expect(grouping!.groups[0].pages.map(p => p.page_id)).toEqual(["p2", "p1"]);
});
it("supports keyboard page selection, typed student search and native select assignment", async () => {
  const user = userEvent.setup(); await ready(); const button = within(screen.getByRole("region", {name: "Страницы партии"})).getByRole("button", {name: /^Страница 3:/}); button.focus(); await user.keyboard("{Enter}");
  await user.type(screen.getByLabelText("Поиск ученика для назначения"), "Петрова"); await user.selectOptions(screen.getByLabelText("Назначить ученику"), "s2"); screen.getByRole("button", {name: "Назначить", exact: true}).focus(); await user.keyboard("{Enter}");
  await waitFor(() => expect(grouping!.unresolved_pages).toHaveLength(0));
});
it("filters roster visually without changing academic membership", async () => {
  await ready(); fireEvent.change(screen.getByLabelText("Найти ученика"), {target: {value: "Сидоров"}}); expect(screen.queryByText("2 стр. · Вариант: А")).toBeNull(); expect(grouping!.groups[0].pages).toHaveLength(2); expect(api.assignScanPage).not.toHaveBeenCalled();
});
it("confirms once, enters read-only summary and navigates to the checking placeholder", async () => {
  await ready(); await choose(3); const confirm = screen.getByRole("button", {name: "Подтвердить распределение"}); fireEvent.click(confirm); fireEvent.click(confirm);
  fireEvent.click(await screen.findByRole("button", {name: "Перейти к проверке"})); expect(api.confirmScanGrouping).toHaveBeenCalledTimes(1); expect(api.confirmScanGrouping).toHaveBeenCalledWith("b", expect.objectContaining({row_version: 2}));
  expect(screen.queryByLabelText("Назначить ученику")).toBeNull(); expect(navigate).toHaveBeenCalledWith("/classrooms/c/assignments/a/paper/b/checking");
});
it("stale CAS clears old state, refetches, shows notice and never resends", async () => {
  await ready(); grouping!.row_version = 9; grouping!.groups[0].pages = [groupingPage("p2", 1)]; grouping!.unresolved_pages.push({...groupingPage("p1", 0), candidate_students: [], reason_code: "teacher_unresolved"});
  await choose(3); await screen.findByText("Распределение изменилось. Мы загрузили актуальные данные."); expect(api.assignScanPage).toHaveBeenCalledTimes(1); expect(grouping!.row_version).toBe(9); expect(screen.getByText("1 из 3 страниц распределено")).toBeTruthy();
});
it("confirmation by another teacher switches stale editing to read-only", async () => {
  await ready(); grouping!.row_version = 9; grouping!.grouping_status = "confirmed"; grouping!.batch_status = status = "ready_for_checking";
  await choose(3); await screen.findByRole("button", {name: "Перейти к проверке"}); expect(screen.queryByRole("button", {name: "Подтвердить распределение"})).toBeNull(); expect(api.assignScanPage).toHaveBeenCalledTimes(1);
});
it.each(["ready_for_checking", "checking", "checking_completed", "completed"])("deep-links to %s as read-only", async advanced => {
  grouping!.grouping_status = "confirmed"; grouping!.batch_status = status = advanced;
  page(); await screen.findByRole("button", {name: "Перейти к проверке"}); expect(screen.queryByLabelText("Назначить ученику")).toBeNull(); expect(api.initializeScanGrouping).not.toHaveBeenCalled(); expect(api.startScanMatching).not.toHaveBeenCalled();
});
it("placeholder only describes checking boundary without execution controls", async () => {
  grouping!.grouping_status = "confirmed"; grouping!.batch_status = status = "ready_for_checking"; page(true);
  await screen.findByRole("heading", {name: "Партия готова к проверке"}); expect(screen.queryByRole("button", {name: /Начать проверку/})).toBeNull();
});
it.each([401, 403, 500])("clears actionable state on read failure %s and bounds private details", async code => {
  await ready(); vi.mocked(api.readScanGrouping).mockRejectedValue(new ApiError(code, "unknown", "SQL asyncpg /private/path")); fireEvent.click(screen.getByRole("button", {name: "Обновить данные"}));
  await screen.findByRole("alert"); expect(screen.queryByRole("button", {name: "Подтвердить распределение"})).toBeNull(); expect(document.body.textContent).not.toContain("SQL");
});
it("rejects foreign batch before reading private grouping or roster", async () => {
  vi.mocked(api.getScanBatch).mockResolvedValue(batchFixture({assignment_id: "foreign"})); page(); await screen.findByRole("alert"); expect(api.readScanGrouping).not.toHaveBeenCalled(); expect(getAssignmentResults).not.toHaveBeenCalled();
});
it("focus revalidates stable drafts and pauses polling while hidden", async () => {
  vi.useFakeTimers(); let hidden = false; vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
  grouping = undefined; status = "matching"; matching = matchingFixture({status: "running", proposals: []}); page(); await act(async () => {});
  hidden = true; await act(async () => {document.dispatchEvent(new Event("visibilitychange")); await vi.advanceTimersByTimeAsync(9000);}); expect(api.readScanMatching).toHaveBeenCalledTimes(1);
  hidden = false; await act(async () => {document.dispatchEvent(new Event("visibilitychange"));}); expect(api.readScanMatching).toHaveBeenCalledTimes(2);
});
it("renders 60 pages, 30 long student names with one roster request and one full image", async () => {
  const roster = Array.from({length: 30}, (_, i) => ({assignment_participant_id: `student-${i}`, student_display_name: `Очень длинное имя ученика ${i} `.repeat(5)}));
  vi.mocked(getAssignmentResults).mockResolvedValue({items: roster} as Awaited<ReturnType<typeof getAssignmentResults>>);
  vi.mocked(api.listScanPages).mockResolvedValue({items: Array.from({length: 60}, (_, i) => pageFixture({id: `page-${i}`, source_page_index: i}))});
  grouping!.groups = roster.map((s, index) => ({assignment_participant_id: s.assignment_participant_id, display_name: s.student_display_name, student_id: "private", assigned_variant_id: "v1", pages: [groupingPage(`page-${index * 2}`, index * 2), groupingPage(`page-${index * 2 + 1}`, index * 2 + 1)]})); grouping!.unresolved_pages = [];
  await ready(); await screen.findByAltText("Страница 1"); expect(within(screen.getByRole("region", {name: "Страницы партии"})).getAllByRole("button")).toHaveLength(60); expect(getAssignmentResults).toHaveBeenCalledTimes(1); expect(api.readScanPageContent).toHaveBeenCalledTimes(1); expect(document.querySelector(".matching-groups-scroll")).toBeTruthy();
});
it.each(["duplicate", "missing", "variant", "participant"])("proactively blocks invalid %s completeness", kind => {
  const g = groupingFixture({unresolved_pages: []}); g.groups[0].pages.push(groupingPage("p3", 2));
  if (kind === "duplicate") g.groups[0].pages.push(groupingPage("p1", 0));
  if (kind === "missing") g.groups[0].pages.pop();
  if (kind === "variant") g.groups[0].assigned_variant_id = "";
  if (kind === "participant") g.groups.push({...g.groups[0]});
  expect(groupingComplete(g, ["p1", "p2", "p3"])).toBe(false);
});
it.each(["unassigned-to-student", "student-to-student", "student-to-unassigned", "reorder"])("drag semantic result: %s", async operation => {
  await ready();
  const pageId = operation === "unassigned-to-student" ? "p3" : "p1";
  const participantId = operation === "student-to-unassigned" ? null : operation === "reorder" ? "s1" : "s2";
  await act(async () => onDragEnd!({active: {id: `strip:${pageId}`, data: {current: {pageId}}}, over: {id: "target", data: {current: {participantId, pageId: operation === "reorder" ? "p2" : undefined}}}} as unknown as import("@dnd-kit/core").DragEndEvent));
  await waitFor(() => expect(grouping!.row_version).toBe(2));
  if (operation === "reorder") expect(api.reorderScanPages).toHaveBeenCalledWith("b", "s1", ["p2", "p1"], expect.objectContaining({row_version: 1}));
  else expect(api.assignScanPage).toHaveBeenCalledWith("b", pageId, participantId, expect.objectContaining({row_version: 1}));
});
it("loads every roster page rather than truncating at the backend default", async () => {
  const participants = Array.from({length: 101}, (_, i) => ({assignment_participant_id: `roster-${i}`, student_display_name: `Ученик ${i}`}));
  vi.mocked(getAssignmentResults).mockImplementation(async (_, __, paging) => ({participant_count: 101, items: participants.slice(paging!.offset, paging!.offset + paging!.limit)} as Awaited<ReturnType<typeof getAssignmentResults>>));
  await ready(); expect(screen.getByText("Ученик 100")).toBeTruthy(); expect(getAssignmentResults).toHaveBeenCalledTimes(2);
  expect(getAssignmentResults).toHaveBeenLastCalledWith("a", expect.any(AbortSignal), {offset: 100, limit: 100});
});
it("never overlaps slow matching reads when focus and refresh repeat", async () => {
  grouping = undefined; status = "matching"; matching = matchingFixture({status: "running", proposals: []});
  let finish!: (m: api.ScanMatching) => void;
  vi.mocked(api.readScanMatching).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
  page(); await waitFor(() => expect(api.readScanMatching).toHaveBeenCalledTimes(1));
  await act(async () => {window.dispatchEvent(new Event("focus")); window.dispatchEvent(new Event("focus"));}); expect(api.readScanMatching).toHaveBeenCalledTimes(1);
  await act(async () => finish(matching!)); await screen.findByRole("heading", {name: "Сопоставляем страницы…"});
});
it("stale final confirmation refetches without forced resubmission", async () => {
  await ready(); await choose(3); grouping!.row_version = 8;
  vi.mocked(api.confirmScanGrouping).mockRejectedValue(new ApiError(409, "grouping_revision_conflict", "private"));
  fireEvent.click(screen.getByRole("button", {name: "Подтвердить распределение"})); await screen.findByText("Распределение изменилось. Мы загрузили актуальные данные.");
  expect(api.confirmScanGrouping).toHaveBeenCalledTimes(1); expect(vi.mocked(api.confirmScanGrouping).mock.calls[0][1].row_version).toBe(2); expect(grouping!.row_version).toBe(8);
});
it("safe navigation aborts pending image reads and ignores late content", async () => {
  let finish!: (blob: Blob) => void;
  vi.mocked(api.readScanPageContent).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
  const view = page(); await screen.findByRole("button", {name: "Подтвердить распределение"}); const signal = vi.mocked(api.readScanPageContent).mock.calls[0][1]!;
  view.unmount(); expect(signal.aborted).toBe(true); await act(async () => finish(new Blob(["image"]))); expect(URL.createObjectURL).not.toHaveBeenCalled();
});
it("keeps a long assignment title and route identity intact", async () => {
  const title = "Очень длинное название самостоятельной работы ".repeat(12); vi.mocked(getAssessment).mockResolvedValue({title, variants: []} as Awaited<ReturnType<typeof getAssessment>>);
  render(<PaperScanPage classId="c" assignmentId="a" batchId="b" matching navigate={navigate}/>); await screen.findByRole("heading", {name: title.trim()}); await screen.findByRole("region", {name: "Страницы партии"});
});
it.each(["draft", "confirmed"])("cancelled batch preserves actual %s status without a checking handoff", async groupStatus => {
  grouping!.grouping_status = groupStatus; grouping!.batch_status = status = "cancelled";
  page(); await screen.findByRole("region", {name: "Страницы партии"});
  expect(screen.queryByRole("button", {name: "Подтвердить распределение"})).toBeNull(); expect(screen.queryByRole("button", {name: "Перейти к проверке"})).toBeNull();
  if (groupStatus === "draft") {expect(screen.getByText("Этап партии изменился. Черновик доступен только для просмотра.")).toBeTruthy(); expect(screen.queryByText(/Распределение подтверждено/)).toBeNull();}
  const button = within(screen.getByRole("region", {name: "Страницы партии"})).getByRole("button", {name: /^Страница 3:/}); expect(button.getAttribute("aria-disabled")).toBeNull(); fireEvent.click(button); await screen.findByAltText("Страница 3");
});
