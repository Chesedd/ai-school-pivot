import {useState} from "react";
import {useDroppable, useDraggable} from "@dnd-kit/core";
import {SortableContext, useSortable, verticalListSortingStrategy} from "@dnd-kit/sortable";
import {CSS} from "@dnd-kit/utilities";
import type {MatchingStudent, PaperGroup, ScanGrouping} from "./paperScanApi";

export function ScanPageButton(p: {id: string; number: number; label: string; selected: boolean; disabled: boolean; onSelect: () => void}) {
  const drag = useDraggable({id: `strip:${p.id}`, data: {pageId: p.id}, disabled: p.disabled});
  return <button ref={drag.setNodeRef} {...drag.listeners} {...drag.attributes} aria-disabled={undefined} aria-roledescription={p.disabled ? undefined : "перетаскиваемая страница"} className={`matching-page-button ${p.selected ? "selected" : ""}`} aria-label={`Страница ${p.number}: ${p.label}`} aria-pressed={p.selected} onClick={p.onSelect}>
    <strong>{p.number}</strong><span>{p.label}</span>
  </button>;
}
function GroupPage(p: {id: string; participantId: string; number: number; index: number; count: number; selected: boolean; editable: boolean; busy: boolean; select: () => void; move: (delta: number) => void}) {
  const drag = useSortable({id: `group-page:${p.id}`, data: {pageId: p.id, participantId: p.participantId}, disabled: !p.editable || p.busy});
  return <li ref={drag.setNodeRef} style={{transform: CSS.Transform.toString(drag.transform), transition: drag.transition}}>
    <button {...drag.listeners} {...drag.attributes} aria-disabled={undefined} aria-roledescription={!p.editable || p.busy ? undefined : "перетаскиваемая страница"} className="secondary" aria-label={`Страница ${p.number} в работе`} aria-pressed={p.selected} onClick={p.select}>{p.number}</button>
    {p.editable && <><button className="link-button" aria-label={`Страница ${p.number}: вверх`} disabled={p.busy || p.index === 0} onClick={() => p.move(-1)}>↑</button>
      <button className="link-button" aria-label={`Страница ${p.number}: вниз`} disabled={p.busy || p.index === p.count - 1} onClick={() => p.move(1)}>↓</button></>}
  </li>;
}
function GroupCard(p: {student: MatchingStudent; group?: PaperGroup; selected: string; editable: boolean; busy: boolean; numbers: Map<string, number>; select: (id: string) => void; reorder: (participantId: string, ids: string[]) => void; variants: Record<string, string>}) {
  const id = p.student.assignment_participant_id;
  const drop = useDroppable({id: `student:${id}`, data: {participantId: id}, disabled: !p.editable || p.busy});
  const pages = p.group?.pages || [];
  const move = (index: number, delta: number) => {
    const ids = pages.map(x => x.page_id);
    [ids[index], ids[index + delta]] = [ids[index + delta], ids[index]];
    p.reorder(id, ids);
  };
  const variant = p.group?.assigned_variant_id && p.variants[p.group.assigned_variant_id];
  return <article ref={drop.setNodeRef} className={`matching-group ${drop.isOver ? "drop-target" : ""} ${pages.length ? "" : "compact"}`}>
    <strong>{p.student.display_name}</strong><small>{pages.length} стр.{variant && ` · Вариант: ${variant}`}</small>
    {!!pages.length && <SortableContext items={pages.map(x => `group-page:${x.page_id}`)} strategy={verticalListSortingStrategy}>
      <ol className="matching-group-pages">{pages.map((page, index) => <GroupPage key={page.page_id} id={page.page_id} participantId={id} number={p.numbers.get(page.page_id) || page.source_order + 1} index={index} count={pages.length} selected={p.selected === page.page_id} editable={p.editable} busy={p.busy} select={() => p.select(page.page_id)} move={delta => move(index, delta)}/>)}</ol>
    </SortableContext>}
  </article>;
}
export function PaperGroupPanel(p: {roster: MatchingStudent[]; grouping: ScanGrouping; selected: string; editable: boolean; busy: boolean; numbers: Map<string, number>; select: (id: string) => void; reorder: (participantId: string, ids: string[]) => void; variants: Record<string, string>}) {
  const [search, setSearch] = useState("");
  const drop = useDroppable({id: "unassigned", data: {participantId: null}, disabled: !p.editable || p.busy});
  return <section className="matching-roster" aria-label="Работы учеников">
    <h2>Работы учеников</h2><label>Найти ученика<input type="search" value={search} onChange={e => setSearch(e.target.value)}/></label>
    <div className="matching-groups-scroll">{p.roster.filter(s => s.display_name.toLocaleLowerCase("ru").includes(search.toLocaleLowerCase("ru"))).map(student => <GroupCard key={student.assignment_participant_id} {...p} student={student} group={p.grouping.groups.find(g => g.assignment_participant_id === student.assignment_participant_id)}/>)}
      {!p.roster.some(s => s.display_name.toLocaleLowerCase("ru").includes(search.toLocaleLowerCase("ru"))) && <p>Ученики не найдены.</p>}
    </div>
    <div ref={drop.setNodeRef} className={`matching-unassigned ${drop.isOver ? "drop-target" : ""}`}><h3>Не распределены · {p.grouping.unresolved_pages.length}</h3>
      <div className="button-row">{p.grouping.unresolved_pages.map(page => <button key={page.page_id} className="secondary" aria-pressed={p.selected === page.page_id} onClick={() => p.select(page.page_id)}>Страница {p.numbers.get(page.page_id)}</button>)}</div>
      {!p.grouping.unresolved_pages.length && <p>Все страницы распределены.</p>}
    </div>
  </section>;
}
