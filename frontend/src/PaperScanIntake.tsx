import {useRef, useState} from "react";
import {downloadScanArtifact, extractScanBatch, uploadScanArtifact, type ScanArtifact} from "./paperScanApi";
import {acceptsUpload, artifactLabels, batchLabels, canExtract, intakeReady, needsPolling, progressed, scanAccept, scanErrorMessage, scanFailureMessage} from "./paperScanState";
import {usePaperScanBatch} from "./usePaperScanBatch";

type Selected = {file: File; state: "selected" | "uploading" | "uploaded" | "failed" | "not_sent"; error?: string};
const selectedLabels = {selected: "Выбран", uploading: "Загрузка…", uploaded: "Загружен", failed: "Загрузка не подтверждена", not_sent: "Не отправлен"};
export function PaperScanIntake(p: {batchId: string; assignmentId: string; participantCount: number; base: string; matching?: boolean; navigate: (to: string) => void}) {
  const [selected, setSelected] = useState<Selected[]>([]);
  const [busy, setBusy] = useState<"upload" | "extract" | "">("");
  const {data, error: readError, refreshing, refresh} = usePaperScanBatch(p.batchId, p.assignmentId, busy === "extract");
  const [error, setError] = useState("");
  const guard = useRef(false);
  const picker = useRef<HTMLInputElement>(null);
  const choose = (files: FileList | null) => {
    if (guard.current || !data || !acceptsUpload(data.batch)) return;
    setSelected(Array.from(files || []).map(file => ({file, state: "selected"}))); setError("");
  };
  const upload = async () => {
    if (guard.current || !data || !acceptsUpload(data.batch) || !selected.length) return;
    guard.current = true; setBusy("upload"); setError("");
    const queue = selected.map(x => ({...x}));
    // The endpoint accepts one file. Every selected file gets an explicit outcome.
    // Stop on rejection/uncertain acknowledgement: the API has no upload idempotency.
    let position = Math.max(-1, ...data.artifacts.map(a => a.upload_position)) + 1;
    try {
      for (let i = 0; i < queue.length; i++) {
        queue[i].state = "uploading"; setSelected(queue.map(x => ({...x})));
        try {
          await uploadScanArtifact(p.batchId, queue[i].file, position++);
          queue[i].state = "uploaded";
        } catch (e) {
          queue[i].state = "failed"; queue[i].error = scanErrorMessage(e);
          for (let j = i + 1; j < queue.length; j++) queue[j].state = "not_sent";
          setError("Загрузка завершилась частично. Проверьте список сохранённых файлов и выберите оставшиеся файлы заново.");
          break;
        } finally {setSelected(queue.map(x => ({...x})));}
      }
      await refresh(true);
    } finally {guard.current = false; setBusy("");}
  };
  const extract = async () => {
    if (guard.current || !data || !canExtract(data)) return;
    guard.current = true; setBusy("extract"); setError("");
    try {await extractScanBatch(p.batchId);} catch (e) {setError(scanErrorMessage(e));}
    finally {await refresh(true); guard.current = false; setBusy("");}
  };
  const back = <a href={p.base} onClick={e => {e.preventDefault(); p.navigate(p.base);}}>← Все партии этой работы</a>;
  if (!data) return <>{back}{readError ? <><p className="alert error" role="alert">{readError}</p><button disabled={refreshing} onClick={() => void refresh()}>Обновить данные</button></> : <p role="status">Загрузка партии…</p>}</>;
  const ready = intakeReady(data) && p.participantCount > 0;
  const later = progressed(data.batch);
  const selectionPending = selected.some(x => x.state !== "uploaded");
  const actionable = !busy && !refreshing && !readError;
  if (p.matching) return <>{back}<section className="solving-status"><h2>Сопоставление бумажных работ</h2><p>{ready || later ? "Этот этап будет доступен в следующем обновлении. Обработка файлов сохранена." : "Сначала завершите обработку файлов."}</p><button onClick={() => p.navigate(`${p.base}/${encodeURIComponent(p.batchId)}`)}>Вернуться к файлам</button></section></>;
  return <>{back}
    <header className="scan-heading"><div><h2>Партия от {new Date(data.batch.created_at).toLocaleString("ru-RU")}</h2><span className="badge">{ready ? "Готово к сопоставлению" : batchLabels[data.batch.status] || "Состояние уточняется"}</span></div><button className="link-button" disabled={!actionable} onClick={() => void refresh()}>Обновить данные</button></header>
    {error && <p className="alert error" role="alert">{error}</p>}
    {acceptsUpload(data.batch) && <section className="scan-upload" onDragOver={e => e.preventDefault()} onDrop={e => {e.preventDefault(); if (actionable) choose(e.dataTransfer.files);}}>
      <h2>Загрузить работы</h2><p>PDF, JPEG, PNG или WebP. Можно выбрать несколько файлов или перетащить их сюда. После загрузки начните обработку; сопоставление учеников идёт следующим этапом.</p>
      <label>Файлы работ<input ref={picker} type="file" accept={scanAccept} multiple disabled={!actionable} onChange={e => choose(e.target.files)}/></label>
      {!!selected.length && <ul className="scan-selection" aria-live="polite">{selected.map((x, i) => <li key={i}><strong>{x.file.name}</strong> — {selectedLabels[x.state]}{x.error && <p role="alert">{x.error}</p>}</li>)}</ul>}
      <div className="button-row"><button disabled={!actionable || !selected.length || selected.some(x => x.state !== "selected")} onClick={() => void upload()}>{busy === "upload" ? "Загрузка файлов…" : "Загрузить выбранные файлы"}</button>{!!selected.length && <button className="secondary" disabled={!actionable} onClick={() => {setSelected([]); if (picker.current) picker.current.value = "";}}>Очистить выбор</button>}</div>
    </section>}
    <section><h2>Загруженные файлы</h2>{data.artifacts.length ? <ul className="scan-artifacts">{data.artifacts.map(a => <ArtifactRow key={a.id} artifact={a} batchId={p.batchId} failedPages={data.pages.filter(page => page.source_artifact_id === a.id && page.status.startsWith("failed")).length}/>)}</ul> : <p>Файлов пока нет.</p>}</section>
    <section className="solving-status" aria-live="polite"><h2>{ready ? "Готово к сопоставлению" : later ? "Загрузка и обработка завершены" : "Обработка файлов"}</h2>
      <p>Сохранено файлов: {data.artifacts.length} · Извлечено страниц: {data.pages.length}</p>
      {(busy === "extract" || needsPolling(data)) && <p>Обрабатываем файлы… Можно вернуться к этой партии позже.</p>}
      {data.pages.some(page => page.status.startsWith("failed")) && <p>Некоторые страницы не обработаны. Переход к сопоставлению недоступен.</p>}
      {data.artifacts.some(a => a.extraction_status === "failed_terminal") && <p>Есть файлы, которые нельзя обработать повторно. Успешно обработанные файлы сохранены.</p>}
      {!p.participantCount && <p>В назначении нет участников. Сопоставление недоступно.</p>}
      {canExtract(data) && <><p>{data.batch.status === "uploading" ? "Убедитесь, что все файлы загружены. После начала обработки добавить файлы в эту партию нельзя." : "Повторная обработка сохранит готовые страницы; файлы с окончательной ошибкой будут пропущены."}</p><button disabled={!actionable || selectionPending} onClick={() => void extract()}>{busy === "extract" ? "Обработка…" : data.batch.status === "uploading" ? "Начать обработку" : "Продолжить обработку"}</button></>}
      {selectionPending && <p>Загрузите выбранные файлы или очистите выбор перед продолжением.</p>}
      {!acceptsUpload(data.batch) && !later && !ready && !canExtract(data) && <p>Переход к сопоставлению недоступен в текущем состоянии партии.</p>}
      {later && <p>Партия уже перешла к следующему этапу. Файлы доступны только для просмотра.</p>}
      <div className="button-row"><button disabled={!actionable || selectionPending || (!ready && !later)} onClick={() => p.navigate(`${p.base}/${encodeURIComponent(p.batchId)}/matching`)}>{later ? "О сопоставлении" : "Перейти к сопоставлению"}</button></div>
    </section>
  </>;
}
function ArtifactRow({artifact: a, batchId, failedPages}: {artifact: ScanArtifact; batchId: string; failedPages: number}) {
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const download = async () => {
    if (busy) return;
    setBusy(true); setMessage("");
    try {
      const blob = await downloadScanArtifact(batchId, a.id);
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = a.original_filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {setMessage(scanErrorMessage(e));} finally {setBusy(false);}
  };
  return <li><div><strong>{a.original_filename}</strong><p className="scan-file-meta">{artifactLabels[a.extraction_status] || "Состояние уточняется"}{a.page_count !== null && ` · Страниц: ${a.page_count}`}</p>{a.failure_code && <p>{scanFailureMessage(a.failure_code)}</p>}{failedPages > 0 && <p>Не обработано страниц: {failedPages}</p>}{message && <p role="status">{message}</p>}</div><button className="link-button" disabled={busy} onClick={() => void download()} aria-label={`Скачать ${a.original_filename}`}>{busy ? "Загрузка…" : "Скачать исходный файл"}</button></li>;
}
