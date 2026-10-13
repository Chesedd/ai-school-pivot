import {useState} from "react";
import {DndContext, PointerSensor, useSensor, useSensors, type DragEndEvent} from "@dnd-kit/core";
import {arrayMove} from "@dnd-kit/sortable";
import * as api from "./paperScanApi";
import {batchLabels, intakeReady} from "./paperScanState";
import {usePaperMatching} from "./usePaperMatching";
import {ScanPageViewer} from "./ScanPageViewer";
import {PaperGroupPanel, ScanPageButton} from "./PaperGroupPanel";

export const groupingEditable = (g: api.ScanGrouping) => g.grouping_status === "draft" && g.batch_status === "matching_review_required";
export function groupingComplete(g: api.ScanGrouping, pageIds: string[]): boolean {
  const assigned = g.groups.flatMap(x => x.pages.map(p => p.page_id));
  return g.unresolved_pages.length === 0 && g.groups.length > 0 && g.groups.every(x => x.pages.length > 0 && !!x.assigned_variant_id) &&
    new Set(g.groups.map(x => x.assignment_participant_id)).size === g.groups.length &&
    assigned.length === pageIds.length && new Set(assigned).size === assigned.length && pageIds.every(id => assigned.includes(id));
}
export function PaperMatchingPage(p: {batchId: string; assignmentId: string; base: string; variants: Record<string, string>; navigate: (to: string) => void; checking?: boolean}) {
  const {data, error, notice, busy, refreshing, refresh, mutate} = usePaperMatching(p.batchId, p.assignmentId);
  const [selection, setSelection] = useState("");
  const [target, setTarget] = useState("");
  const [search, setSearch] = useState("");
  const sensors = useSensors(useSensor(PointerSensor, {activationConstraint: {distance: 8}}));
  const intake = `${p.base}/${encodeURIComponent(p.batchId)}`;
  const back = <a href={intake} onClick={e => {e.preventDefault(); p.navigate(intake);}}>← К файлам партии</a>;
  if (!data) return <>{back}{error ? <p className="alert error" role="alert">{error}</p> : <p role="status">Загрузка сопоставления…</p>}<button className="secondary" disabled={refreshing || busy} onClick={() => void refresh()}>Обновить данные</button></>;
  const g = data.grouping;
  const editable = !!g && groupingEditable(g) && data.batch.status === "matching_review_required" && !p.checking;
  const blocked = busy || refreshing;
  const pages = [...data.pages].sort((a, b) => {
    const artifacts = data.artifacts;
    return (artifacts.find(x => x.id === a.source_artifact_id)?.upload_position || 0) - (artifacts.find(x => x.id === b.source_artifact_id)?.upload_position || 0) || a.source_page_index - b.source_page_index;
  });
  const numbers = new Map(pages.map((page, index) => [page.id, index + 1]));
  const selected = pages.find(x => x.id === selection) || pages[0];
  const owner = selected && g?.groups.find(x => x.pages.some(page => page.page_id === selected.id));
  const proposal = selected && data.matching?.proposals.find(x => x.page_id === selected.id);
  const assign = (pageId: string, participantId: string | null) => {if (g && editable && !blocked) void mutate(() => api.assignScanPage(p.batchId, pageId, participantId, g));};
  const reorder = (participantId: string, ids: string[]) => {if (g && editable && !blocked) void mutate(() => api.reorderScanPages(p.batchId, participantId, ids, g));};
  const dragEnd = ({active, over}: DragEndEvent) => {
    if (!over || !editable || blocked || !g) return;
    const pageId = active.data.current?.pageId as string | undefined;
    if (!pageId) return;
    const from = g.groups.find(x => x.pages.some(page => page.page_id === pageId));
    const participantId = over.data.current?.participantId as string | null | undefined;
    if (participantId === undefined) return;
    const targetPage = over.data.current?.pageId as string | undefined;
    if (from?.assignment_participant_id === participantId) {
      if (targetPage && targetPage !== pageId) {
        const ids = from.pages.map(x => x.page_id);
        reorder(participantId, arrayMove(ids, ids.indexOf(pageId), ids.indexOf(targetPage)));
      }
    } else assign(pageId, participantId);
  };
  const complete = !!g && groupingComplete(g, pages.map(x => x.id));
  const advanced = !!g && !groupingEditable(g);
  const confirmed = g?.grouping_status === "confirmed";
  const handoff = confirmed && ["grouping_confirmed", "ready_for_checking", "checking", "checking_completed", "completed"].includes(data.batch.status);
  const running = data.matching?.status === "running" || busy && !g;
  const retryable = data.matching?.status === "failed_retryable" && data.batch.status === "matching";
  const terminal = data.matching?.status === "failed_terminal";
  const canStart = !data.matching && intakeReady(data) && data.roster.length > 0;
  const assigned = g ? new Set(g.groups.flatMap(x => x.pages.map(page => page.page_id))).size : 0;
  const select = (id: string) => {setSelection(id); setTarget(""); setSearch("");};
  return <>{back}<header className="matching-header"><div><h2>{p.checking ? "Проверка бумажных работ" : "Сопоставление"}</h2><small>Партия от {new Date(data.batch.created_at).toLocaleString("ru-RU")} · {batchLabels[data.batch.status] || "Состояние уточняется"}</small></div><button className="secondary" disabled={blocked} onClick={() => void refresh()}>Обновить данные</button></header>
    {notice && <p role="status" className="matching-notice">{notice}</p>}
    {p.checking ? <section className="solving-status"><h3>{data.batch.status === "ready_for_checking" ? "Партия готова к проверке" : batchLabels[data.batch.status] || "Состояние уточняется"}</h3><p>Этап проверки будет доступен в следующем обновлении.</p><button className="secondary" onClick={() => p.navigate(`${intake}/matching`)}>К распределению страниц</button></section> : <>
      {!g && <section className="solving-status" aria-live="polite"><h3>{running ? "Сопоставляем страницы…" : retryable ? "Не удалось сопоставить страницы" : terminal ? "Сопоставление остановлено" : "Сопоставление страниц"}</h3>
        <p>{running ? "Можно вернуться к партии позже. Обработка сохранена." : retryable ? "Можно продолжить сопоставление. Уже обработанные страницы сохранены." : terminal ? "Повторное сопоставление этой партии недоступно." : canStart ? "Сопоставление предложит распределение. Затем проверьте и подтвердите его." : "Сначала завершите обработку файлов. Сопоставление пока недоступно."}</p>
        {(canStart || retryable) && <button disabled={blocked} onClick={() => void mutate(() => api.startScanMatching(p.batchId), true)}>{retryable ? "Продолжить сопоставление" : "Сопоставить страницы"}</button>}
      </section>}
      {g && <><p className="matching-guidance">{confirmed ? `Распределение подтверждено · Работ: ${g.paper_submissions.length} · Страниц: ${pages.length}` : advanced ? "Этап партии изменился. Черновик доступен только для просмотра." : "Проверьте распределение страниц перед подтверждением. Изменения сохраняются сразу."}</p>
        <DndContext sensors={sensors} onDragEnd={dragEnd} accessibility={{
          screenReaderInstructions: {draggable: "Для назначения выберите страницу и ученика. Для изменения порядка используйте кнопки вверх и вниз в работе ученика."},
          announcements: {
            onDragStart: ({active}) => `Перемещение страницы ${numbers.get(active.data.current?.pageId) || ""}.`,
            onDragOver: ({over}) => over ? over.data.current?.participantId === null ? "Не распределены." : data.roster.find(s => s.assignment_participant_id === over.data.current?.participantId)?.display_name : undefined,
            onDragEnd: () => "Перемещение завершено. Проверяем сохранённое распределение.",
            onDragCancel: () => "Перемещение отменено.",
          },
        }}><div className="matching-workspace">
          <section className="matching-pages" aria-label="Страницы партии"><h2>Страницы · {pages.length}</h2><div className="matching-pages-scroll">{pages.map(page => {
            const group = g.groups.find(x => x.pages.some(pg => pg.page_id === page.id));
            const suggestion = data.matching?.proposals.find(x => x.page_id === page.id);
            const status = confirmed && group ? "Назначена" : group ? "В черновике" : "Не распределена";
            const warning = suggestion?.warning || g.unresolved_pages.some(x => x.page_id === page.id && ["ambiguous", "invalid_match"].includes(x.reason_code));
            return <ScanPageButton key={page.id} id={page.id} number={numbers.get(page.id)!} label={`${group?.display_name || status}${group ? ` · ${status}` : ""}${warning && editable ? " · Нужно проверить" : ""}`} selected={selected?.id === page.id} disabled={!editable || blocked} onSelect={() => select(page.id)}/>;
          })}</div></section>
          <section className="matching-viewer" aria-label="Просмотр страницы">{selected ? <><ScanPageViewer key={selected.id} page={selected} number={numbers.get(selected.id)!}/>
            <p className="matching-assignment-context">{owner ? `Работа: ${owner.display_name}` : "Не распределена"}</p>
            {editable && proposal && <p className="matching-proposal">{proposal.proposed_student ? `Предложено: ${proposal.proposed_student.display_name}` : proposal.disposition === "ambiguous" ? "Нужно проверить: неоднозначное сопоставление" : "Ученик не определён"}{proposal.warning && " · Нужно проверить"}{proposal.candidate_students.length > 0 && ` · Возможные ученики: ${proposal.candidate_students.map(s => s.display_name).join(", ")}`}</p>}
            {editable && <div className="matching-assignment-controls"><label>Поиск ученика для назначения<input type="search" value={search} onChange={e => {setSearch(e.target.value); setTarget("");}} disabled={blocked}/></label>
              <label>Назначить ученику<select value={target} onChange={e => setTarget(e.target.value)} disabled={blocked}><option value="">Выберите ученика</option>{data.roster.filter(s => s.display_name.toLocaleLowerCase("ru").includes(search.toLocaleLowerCase("ru"))).map(s => <option key={s.assignment_participant_id} value={s.assignment_participant_id}>{s.display_name} · {g.groups.find(gr => gr.assignment_participant_id === s.assignment_participant_id)?.pages.length || 0} стр.</option>)}</select></label>
              <div className="button-row"><button className="secondary" disabled={blocked || !target || target === owner?.assignment_participant_id} onClick={() => assign(selected.id, target)}>Назначить</button><button className="secondary" disabled={blocked || !owner} onClick={() => assign(selected.id, null)}>В нераспределённые</button></div>
            </div>}</> : <p>Страниц пока нет.</p>}</section>
          <PaperGroupPanel roster={data.roster} grouping={g} selected={selected?.id || ""} editable={editable} busy={blocked} numbers={numbers} select={select} reorder={reorder} variants={p.variants}/>
        </div></DndContext>
        <footer className="matching-footer"><div><strong>{assigned} из {pages.length} страниц распределено</strong><p role="status">{busy ? "Сохраняем изменения…" : editable ? complete ? "Все страницы распределены. Можно подтвердить." : "Распределите все страницы перед подтверждением." : "Распределение доступно только для просмотра."}</p></div>
          {editable ? <button disabled={blocked || !complete} onClick={() => void mutate(() => api.confirmScanGrouping(p.batchId, g))}>Подтвердить распределение</button> : handoff && <button onClick={() => p.navigate(`${intake}/checking`)}>Перейти к проверке</button>}
        </footer></>}
    </>}
  </>;
}
