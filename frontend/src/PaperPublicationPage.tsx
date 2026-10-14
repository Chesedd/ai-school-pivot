import {useCallback, useEffect, useRef, useState, type ReactNode} from "react";
import {ApiError} from "./api";
import * as api from "./paperPublicationApi";
import {getScanBatch} from "./paperScanApi";
import {scanErrorMessage} from "./paperScanState";
import {publicationState, publicationLabels, publishLabel} from "./paperPublicationState";
import "./paperPublication.css";
import {Dialog} from "./StatusActions";

// Shared modal primitive traps focus and restores the opener.
function PublicationDialog(p: {title: string; busy: boolean; close: () => void; children: ReactNode}) {
  return <Dialog title={p.title} busy={p.busy} onClose={p.close}><div className="publication-dialog">{p.children}</div></Dialog>;
}

export function PaperPublicationPage(p: {batchId: string; assignmentId: string; base: string; navigate: (to: string) => void}) {
  const [data, setData] = useState<api.PublicationProgress>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  // Selection stores both identities at selection time; refetch clears it.
  const [selected, setSelected] = useState<api.PublicationPaper[]>([]);
  const [histories, setHistories] = useState<Record<string, api.PublicationHistory[]>>({});
  const [historyPaper, setHistoryPaper] = useState<api.PublicationPaper>();
  const [historyError, setHistoryError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [withdraw, setWithdraw] = useState<api.WithdrawalEntry[]>();
  const [bulkWithdrawal, setBulkWithdrawal] = useState(false);
  const closeWithdraw = useCallback(() => setWithdraw(undefined), []);
  const [reason, setReason] = useState("");
  const [completing, setCompleting] = useState(false);
  const closeComplete = useCallback(() => setCompleting(false), []);
  const lock = useRef(false);
  const allowed = useRef(false);
  const historySerial = useRef(0);
  const [retryAvailable, setRetryAvailable] = useState(false);
  const retry = useRef<api.PublicationCommand<unknown> | undefined>(undefined);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const value = await api.readPublicationProgress(p.batchId, signal);
      if (!signal?.aborted) {setData(value); setSelected([]);}
    } finally {if (!signal?.aborted) setLoading(false);}
  }, [p.batchId]);
  useEffect(() => {
    const c = new AbortController(); allowed.current = false;
    (async () => {
      try {
        const batch = await getScanBatch(p.batchId, c.signal);
        if (batch.assignment_id !== p.assignmentId) throw new ApiError(404, "scan_batch_not_found", "");
        if (!c.signal.aborted) {allowed.current = true; await refresh(c.signal);}
      } catch (e) {if (!c.signal.aborted) setError(scanErrorMessage(e));}
    })();
    return () => c.abort();
  }, [p.batchId, p.assignmentId, refresh]);
  const reload = async () => {if (!allowed.current) return; try {await refresh(); setError("");} catch (e) {setError(scanErrorMessage(e));}};
  const act = async (create: () => api.PublicationCommand<unknown>) => {
    if (lock.current || !allowed.current || retry.current) return;
    try {await run(create());} catch (e) {setError(scanErrorMessage(e));}
  };
  const run = async (command: api.PublicationCommand<unknown>) => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    let failure: unknown;
    try {
      await command.run(); retry.current = undefined; setRetryAvailable(false);
      setWithdraw(undefined); setCompleting(false); setHistories({}); setHistoryPaper(undefined);
    } catch (e) {
      failure = e;
      if (!(e instanceof ApiError) || e.status >= 500) {retry.current = command; setRetryAvailable(true);}
      else {retry.current = undefined; setRetryAvailable(false); setWithdraw(undefined);}
    }
    setHistories({});
    try {await refresh();} catch (e) {if (!failure) failure = e;}
    if (failure) setError(scanErrorMessage(failure));
    lock.current = false; setBusy(false);
  };
  const openHistory = async (row: api.PublicationPaper) => {
    const serial = ++historySerial.current;
    setHistoryPaper(row); setHistoryError(""); setHistoryLoading(true);
    try {
      const result = await api.readPublicationHistory(row.paper_submission_id);
      if (serial === historySerial.current) setHistories(old => ({...old, [row.paper_submission_id]: result.items}));
    } catch (e) {if (serial === historySerial.current) setHistoryError(scanErrorMessage(e));}
    finally {if (serial === historySerial.current) setHistoryLoading(false);}
  };
  const closeHistory = useCallback(() => {historySerial.current++; setHistoryPaper(undefined);}, []);
  const confirmWithdraw = () => void act(() => !bulkWithdrawal
    ? api.prepareWithdraw(withdraw![0].paper_submission_id, withdraw![0].publication_id, reason)
    : api.prepareSelectedWithdraw(p.batchId, withdraw!, reason));
  const complete = async () => {
    if (lock.current || retry.current) return;
    lock.current = true; setBusy(true); setError("");
    let failure: unknown;
    try {await api.completePaperBatch(p.batchId); setCompleting(false);} catch (e) {failure = e;}
    try {await refresh();} catch (e) {if (!failure) failure = e;}
    if (failure) setError(scanErrorMessage(failure));
    lock.current = false; setBusy(false);
  };
  const disabled = busy || loading || retryAvailable;
  if (!data) return <section><h2>Публикация результатов</h2>{error ? <p role="alert">{error}</p> : <p role="status">Загрузка публикаций…</p>}<button onClick={() => void reload()} disabled={loading}>Обновить данные</button></section>;
  const visible = data.papers.filter(row => row.display_name.toLocaleLowerCase("ru").includes(search.toLocaleLowerCase("ru")) && (filter === "all" || publicationState(row) === filter));
  const candidates = visible.filter(row => !!row.latest_approved_revision_id || !!row.active_publication_id);
  const publishable = selected.filter(row => !!row.latest_approved_revision_id && publicationState(row) !== "published");
  const active = selected.filter(row => !!row.active_publication_id);
  const beginWithdraw = (rows: api.PublicationPaper[], bulk = false) => {setBulkWithdrawal(bulk); setReason(""); setWithdraw(rows.map(row => ({paper_submission_id: row.paper_submission_id, publication_id: row.active_publication_id!})));};
  return <section className="publication-workspace">
    <header className="matching-header"><h2>Публикация результатов</h2><button className="secondary" disabled={busy || loading} onClick={() => void reload()}>Обновить данные</button></header>
    <div className="checking-summary"><p>Опубликовано {data.active_publication_count} из {data.paper_count}</p><p>Готовы: {data.papers.filter(x => publicationState(x) === "ready").length} · Новая версия: {data.papers.filter(x => publicationState(x) === "new").length} · Требуют проверки: {data.papers.filter(x => publicationState(x) === "review").length}</p>
      {data.batch_status === "completed" ? <strong role="status">Партия завершена</strong> : <><button disabled={disabled || !data.can_finalize} onClick={() => setCompleting(true)}>Завершить партию</button>{!data.can_finalize && <p>Завершение доступно после окончания проверки и публикации всех работ.</p>}</>}
      <button className="secondary" onClick={() => p.navigate(`${p.base}/${encodeURIComponent(p.batchId)}/checking`)}>Открыть проверку</button>
    </div>
    {error && <p role="alert" className="alert error">{error}</p>}
    {retryAvailable && <div role="status"><p>Ответ команды не подтверждён. Повторите ту же команду, чтобы уточнить результат.</p><button disabled={busy || loading} onClick={() => retry.current && void run(retry.current)}>Повторить команду</button></div>}
    <div className="publication-tools"><label>Поиск ученика<input type="search" value={search} onChange={e => setSearch(e.target.value)}/></label><label>Состояние<select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">Все</option><option value="ready">Готовы</option><option value="published">Опубликованы</option><option value="new">Новая версия</option><option value="review">Требуют проверки</option></select></label></div>
    <div className="publication-bulk"><label><input type="checkbox" aria-label="Выбрать доступные работы" disabled={disabled || !candidates.length} checked={!!candidates.length && candidates.every(x => selected.some(y => y.paper_submission_id === x.paper_submission_id))} onChange={e => setSelected(e.target.checked ? candidates : [])}/> Выбрать доступные работы</label><span>Выбрано: {selected.length}</span>
      <button disabled={disabled || !publishable.length} onClick={() => void act(() => api.prepareSelectedPublish(p.batchId, publishable.map(x => ({paper_submission_id: x.paper_submission_id, approved_review_revision_id: x.latest_approved_revision_id!}))))}>Опубликовать выбранные ({publishable.length})</button>
      <button className="danger" disabled={disabled || !active.length} onClick={() => beginWithdraw(active, true)}>Отозвать выбранные ({active.length})</button>
    </div>
    <ul className="publication-list">{visible.map(row => {
      const state = publicationState(row);
      return <li key={row.paper_submission_id} className="publication-row"><label className="publication-select"><input type="checkbox" aria-label={`Выбрать работу: ${row.display_name}`} disabled={disabled || !row.latest_approved_revision_id && !row.active_publication_id} checked={selected.some(x => x.paper_submission_id === row.paper_submission_id)} onChange={e => setSelected(old => e.target.checked ? [...old, row] : old.filter(x => x.paper_submission_id !== row.paper_submission_id))}/><strong>{row.display_name}</strong></label>
        <div className="publication-status"><strong>{publicationLabels[state]}</strong>{row.review_state === "reviewing" && <small>Есть неподтверждённый черновик</small>}<small>Подтверждённая версия: {row.latest_approved_revision_id || "нет"}</small>{row.active_publication_id && <><small>Публикация: {row.active_publication_id}</small><small>Опубликованная версия: {row.active_publication_approved_review_revision_id}</small></>}</div>
        <div className="publication-actions">{row.latest_approved_revision_id && state !== "published" && <button disabled={disabled} aria-label={`${publishLabel(row, histories[row.paper_submission_id])}: ${row.display_name}`} onClick={() => void act(() => api.preparePublish(row.paper_submission_id, row.latest_approved_revision_id!))}>{publishLabel(row, histories[row.paper_submission_id])}</button>}
          {row.active_publication_id && <button className="danger" disabled={disabled} aria-label={`Отозвать публикацию: ${row.display_name}`} onClick={() => beginWithdraw([row])}>Отозвать публикацию</button>}
          <button className="secondary" disabled={busy} aria-label={`История публикаций: ${row.display_name}`} onClick={() => void openHistory(row)}>История</button><button className="secondary" onClick={() => p.navigate(`${p.base}/${encodeURIComponent(p.batchId)}/checking?paper=${encodeURIComponent(row.paper_submission_id)}`)}>Открыть проверку</button>
        </div></li>;
    })}</ul>{!visible.length && <p>Работ по этому фильтру нет.</p>}
    {withdraw && <PublicationDialog title="Отозвать публикацию" busy={busy} close={closeWithdraw}><p>Результат перестанет быть доступен ученику. История публикации сохранится.</p><p>Работ: {withdraw.length}</p><label>Причина отзыва<textarea disabled={busy || retryAvailable} value={reason} onChange={e => setReason(e.target.value)}/></label><small>{[...reason.trim()].length} / 500</small>{error && <p role="alert">{error}</p>}<div className="dialog-actions"><button className="secondary" disabled={busy} onClick={() => setWithdraw(undefined)}>Отмена</button><button className="danger" disabled={disabled || !reason.trim() || [...reason.trim()].length > 500} onClick={confirmWithdraw}>Подтвердить отзыв</button></div></PublicationDialog>}
    {completing && <PublicationDialog title="Завершить партию" busy={busy} close={closeComplete}><p>После завершения для этой партии нельзя запускать новую проверку. Проверки и публикации уже полученных результатов можно будет корректировать.</p>{error && <p role="alert">{error}</p>}<div className="dialog-actions"><button className="secondary" disabled={busy} onClick={() => setCompleting(false)}>Отмена</button><button disabled={disabled || !data.can_finalize} onClick={() => void complete()}>Подтвердить завершение</button></div></PublicationDialog>}
    {historyPaper && <PublicationDialog title={`История публикаций: ${historyPaper.display_name}`} busy={false} close={closeHistory}>{historyLoading ? <p role="status">Загрузка истории…</p> : historyError ? <p role="alert">{historyError}</p> : <ol className="publication-history">{histories[historyPaper.paper_submission_id]?.map(x => <li key={x.id}><strong>Публикация {x.publication_number} · {x.is_withdrawn ? "Отозвана" : x.is_active ? "Активна" : "Заменена новой"}</strong><p>{new Date(x.published_at).toLocaleString("ru-RU")}</p><p>Подтверждённая версия: {x.approved_review_revision_id}</p>{x.is_withdrawn && <><p>Отозвана: {x.withdrawn_at && new Date(x.withdrawn_at).toLocaleString("ru-RU")} · Учитель: {x.withdrawn_by_user_id}</p><p>Причина отзыва: {x.withdrawal_reason}</p></>}</li>)}</ol>}{!historyLoading && !historyError && !histories[historyPaper.paper_submission_id]?.length && <p>Публикаций пока нет.</p>}<button className="secondary" onClick={closeHistory}>Закрыть</button></PublicationDialog>}
  </section>;
}
