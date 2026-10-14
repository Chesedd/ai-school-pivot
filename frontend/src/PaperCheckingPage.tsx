import {useCallback, useRef, useState} from "react";
import * as api from "./paperReviewApi";
import {batchLabels} from "./paperScanState";
import {canRetryPaper, paperLabel} from "./paperReviewState";
import {usePaperChecking} from "./usePaperChecking";
import {ScanPageViewer} from "./ScanPageViewer";
import {PaperReviewPanel} from "./PaperReviewPanel";

export function PaperCheckingPage(p: {batchId: string; assignmentId: string; base: string; navigate: (to: string) => void; onDirtyChange?: (dirty: boolean) => void}) {
  const {data, error, notice, busy, refresh, mutate} = usePaperChecking(p.batchId, p.assignmentId);
  const [selection, setSelection] = useState(() => new URLSearchParams(location.search).get("paper") || "");
  const [pageSelection, setPageSelection] = useState("");
  const [itemSelection, setItemSelection] = useState("");
  const [findingSelection, setFindingSelection] = useState("");
  const [findings, setFindings] = useState<api.ReviewFinding[]>([]);
  const [pickPoint, setPickPoint] = useState(false);
  const [point, setPoint] = useState<api.FindingRegion | null>(null);
  const [coordinates, setCoordinates] = useState({x: "", y: ""});
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [executionNotice, setExecutionNotice] = useState("");
  const dirty = useRef(false);
  const retryKeys = useRef(new Map<string, string>());
  const onDirtyChange = useCallback((value: boolean) => {dirty.current = value; p.onDirtyChange?.(value);}, [p.onDirtyChange]);
  const beginPick = useCallback((value: boolean) => {setPickPoint(value); setPoint(null); setCoordinates({x: "", y: ""});}, []);
  const choosePoint = useCallback((region: api.FindingRegion) => {setPoint(region); setCoordinates({x: String(region.x), y: String(region.y)});}, []);
  const onFinding = useCallback((f: api.ReviewFinding) => {
    setFindingSelection(f.finding_id); setItemSelection(f.assessment_item_id || ""); setPageSelection(f.scan_page_id);
    setPoint(null); setCoordinates({x: "", y: ""});
    if (f.assessment_item_id) document.getElementById(`review-item-${f.assessment_item_id}`)?.scrollIntoView?.({block: "nearest", behavior: "smooth"});
  }, []);
  const execute = async () => {
    setExecutionNotice("");
    const result = await api.executeChecking(p.batchId);
    if (result.outcomes.some(o => o.code === "paper_check_capacity_unavailable")) setExecutionNotice("Работы ожидают свободного места в очереди. Можно продолжить позже; повторная попытка не потрачена.");
    else if (result.outcomes.some(o => o.code)) setExecutionNotice("Часть работ пока не проверена. Состояния обновлены; выберите работу для доступных действий.");
    else if (result.progress.pending) setExecutionNotice("Часть работ ожидает очереди. Нажмите «Продолжить проверку», чтобы обработать следующую группу.");
    if (result.progress.can_reconcile) await api.reconcileChecking(p.batchId);
  };
  if (!data) return <section><h2>Проверка работ</h2>{error ? <><p role="alert" className="alert error">{error}</p><button className="secondary" onClick={() => void refresh()}>Обновить данные</button></> : <p role="status">Загрузка проверки…</p>}</section>;
  const progress = data.progress;
  const selected = progress.papers.find(x => x.paper_submission_id === selection) || progress.papers[0];
  const group = selected && data.grouping.groups.find(g => g.assignment_participant_id === selected.assignment_participant_id);
  const pages = group?.pages.map(pg => data.pages.find(page => page.id === pg.page_id)).filter(x => !!x) || [];
  const page = pages.find(x => x.id === pageSelection) || pages[0];
  const visible = progress.papers.filter(row => row.display_name.toLocaleLowerCase("ru").includes(search.toLocaleLowerCase("ru")) &&
    (filter === "all" || filter === "approved" && row.review_state === "approved" || filter === "active" && ["pending", "running"].includes(row.checking_state) ||
      filter === "errors" && ["failed_retryable", "failed_terminal"].includes(row.checking_state) || filter === "review" && row.review_state !== "approved" && (row.ai_review_available || row.manual_review_available)));
  const selectPaper = (id: string) => {
    if (id === selected?.paper_submission_id) return;
    if (dirty.current && !confirm("Есть несохранённые изменения. Перейти к другой работе?")) return;
    setSelection(id); setPageSelection(""); setItemSelection(""); setFindingSelection(""); setFindings([]); setPickPoint(false); setPoint(null);
  };
  return <>
    <header className="matching-header"><div><h2>Проверка работ</h2><small>Партия от {new Date(data.batch.created_at).toLocaleString("ru-RU")} · {batchLabels[progress.batch_status] || "Состояние уточняется"}</small></div><button className="secondary" disabled={busy} onClick={() => void refresh()}>Обновить данные</button></header>
    <div className="checking-summary"><p>Проверено: {progress.completed + progress.completed_with_review_required + progress.manual_approved} из {progress.paper_count} · В очереди: {progress.pending} · Проверяются: {progress.running} · Подтверждены: {progress.papers.filter(x => x.review_state === "approved").length}</p>
      <div className="button-row"><button className="secondary" onClick={() => p.navigate(`${p.base}/${encodeURIComponent(p.batchId)}/publications`)}>Публикация результатов</button>{["ready_for_checking", "checking"].includes(progress.batch_status) && progress.not_started > 0 && <button disabled={busy} onClick={() => void mutate(async () => {await api.startChecking(p.batchId); await refresh(); await execute();})}>{busy ? "Проверяем…" : "Начать проверку"}</button>}
        {progress.papers.some(x => x.can_execute) && <button disabled={busy} onClick={() => void mutate(execute)}>{busy ? "Проверяем…" : "Продолжить проверку"}</button>}
        <button className="secondary" onClick={() => p.navigate(`${p.base}/${encodeURIComponent(p.batchId)}/matching`)}>К распределению страниц</button>
      </div><small>Проверка выполняется группами. Очередь сохранена на сервере; её можно продолжить после возвращения.</small>
    </div>
    {error && <p role="alert" className="alert error">{error}</p>}
    {(notice || executionNotice) && <p role="status" className="matching-notice">{notice || executionNotice}</p>}
    <div className="checking-workspace">
      <section className="checking-papers" aria-label="Работы учеников"><h2>Работы · {progress.paper_count}</h2>
        <label>Поиск ученика<input type="search" value={search} onChange={e => setSearch(e.target.value)}/></label>
        <label>Состояние<select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">Все</option><option value="review">Требуют проверки</option><option value="active">Проверяются</option><option value="errors">Ошибки</option><option value="approved">Подтверждены</option></select></label>
        <ul className="checking-paper-list">{visible.map(row => <li key={row.paper_submission_id}><button className={`matching-page-button ${row.paper_submission_id === selected?.paper_submission_id ? "selected" : ""}`} aria-pressed={row.paper_submission_id === selected?.paper_submission_id} disabled={busy} onClick={() => selectPaper(row.paper_submission_id)}><span><strong>{row.display_name}</strong>{" "}<small>{paperLabel(row)}</small></span></button></li>)}</ul>
        {!visible.length && <p>Работ по этому фильтру нет.</p>}
        {selected && canRetryPaper(selected) && <button className="secondary" disabled={busy} onClick={() => void mutate(async () => {
          const run = selected.effective_check_run_id!;
          if (!retryKeys.current.has(run)) retryKeys.current.set(run, crypto.randomUUID());
          await api.retryPaper(selected, retryKeys.current.get(run)!); await refresh(); await execute();
        })}>Повторить проверку</button>}
      </section>
      <section className="checking-viewer" aria-label="Страницы текущей работы"><h2>{selected?.display_name || "Работа"}</h2>
        {page ? <>
          <label>Страница работы<select value={page.id} onChange={e => {setPageSelection(e.target.value); setPoint(null); setCoordinates({x: "", y: ""});}}>{pages.map((x, n) => <option key={x.id} value={x.id}>Страница {n + 1} из {pages.length}</option>)}</select></label>
          <ScanPageViewer key={page.id} page={page} number={pages.indexOf(page) + 1} findings={findings} selectedFinding={findingSelection} onFinding={onFinding} pickPoint={pickPoint} onPoint={choosePoint}/>
          {pickPoint && <fieldset><legend>Место замечания на странице</legend><p>Нажмите на изображение или задайте точку от 0 до 1.</p>{["x", "y"].map(axis => <label key={axis}>Координата {axis}<input type="number" min="0" max="1" step="0.01" value={coordinates[axis as "x" | "y"]} onChange={e => {
            const next = {...coordinates, [axis]: e.target.value}; setCoordinates(next);
            if ([next.x, next.y].every(v => v !== "" && Number.isFinite(Number(v)) && Number(v) >= 0 && Number(v) <= 1)) setPoint({kind: "point", coordinate_space: "normalized_upright_v1", x: Number(next.x), y: Number(next.y)});
            else setPoint(null);
          }}/></label>)}</fieldset>}
        </> : <p>Страницы работы недоступны.</p>}
      </section>
      {selected ? <PaperReviewPanel key={`${selected.paper_submission_id}-${selected.effective_check_run_id || "manual"}`} paper={selected} pageIds={pages.map(x => x.id)} pageId={page?.id || ""} selectedItem={itemSelection} selectedFinding={findingSelection} point={point}
        onSelectItem={setItemSelection} onFinding={onFinding} onEvidence={setFindings} onPickPoint={beginPick} onDirtyChange={onDirtyChange} refreshProgress={refresh}/> : <section><p>В партии пока нет подтверждённых работ.</p></section>}
    </div>
  </>;
}
