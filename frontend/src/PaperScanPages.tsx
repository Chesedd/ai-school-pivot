import {useEffect, useRef, useState} from "react";
import {ApiError, getAssessment, getTeacherAssignment} from "./api";
import {createScanBatch, listScanBatches, type ScanBatch} from "./paperScanApi";
import {batchLabels, scanErrorMessage} from "./paperScanState";
import {PaperScanIntake} from "./PaperScanIntake";
import {PaperMatchingPage} from "./PaperMatchingPage";

const paperPath = (classId: string, assignmentId: string) => `/classrooms/${encodeURIComponent(classId)}/assignments/${encodeURIComponent(assignmentId)}/paper`;
export function PaperScanPage(p: {classId: string; assignmentId: string; batchId?: string; matching?: boolean; checking?: boolean; navigate: (to: string) => void}) {
  const [context, setContext] = useState<{title: string; participantCount: number; variants: Record<string, string>}>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setContext(undefined); setError("");
    (async () => {
      try {
        const assignment = await getTeacherAssignment(p.assignmentId, controller.signal);
        if (assignment.class_group_id !== p.classId) throw new ApiError(404, "assignment_not_found", "");
        const assessment = await getAssessment(assignment.assessment_id, controller.signal);
        if (!controller.signal.aborted) setContext({title: assessment.title, participantCount: assignment.participant_count, variants: Object.fromEntries((assessment.variants || []).map(v => [v.id, v.name]))});
      } catch (e) {if (!controller.signal.aborted) setError(scanErrorMessage(e));}
    })();
    return () => controller.abort();
  }, [p.assignmentId, p.classId]);
  const base = paperPath(p.classId, p.assignmentId);
  return <div className={`paper-scan ${p.matching || p.checking ? "paper-matching" : ""}`}>
    <a className="back" href={`/classrooms/${encodeURIComponent(p.classId)}`} onClick={e => {e.preventDefault(); p.navigate(e.currentTarget.pathname);}}>← К назначениям класса</a>
    {error ? <p role="alert" className="alert error">{error}</p> : !context ? <p role="status">Загрузка работы…</p> : <>
      <h1>{context.title}</h1><p className="scan-context">Бумажные работы</p>
      {p.batchId ? p.matching || p.checking ? <PaperMatchingPage key={`${p.batchId}-${!!p.checking}`} batchId={p.batchId} assignmentId={p.assignmentId} base={base} variants={context.variants} checking={p.checking} navigate={p.navigate}/> : <PaperScanIntake key={p.batchId} batchId={p.batchId} assignmentId={p.assignmentId} participantCount={context.participantCount} base={base} navigate={p.navigate}/> :
        <ScanBatchList key={p.assignmentId} assignmentId={p.assignmentId} base={base} navigate={p.navigate}/>}
    </>}
  </div>;
}
function ScanBatchList(p: {assignmentId: string; base: string; navigate: (to: string) => void}) {
  const [batches, setBatches] = useState<ScanBatch[]>();
  const [error, setError] = useState("");
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const guard = useRef(false);
  // Retain the same command identity after an uncertain response. Editing the
  // payload deliberately creates a new command; no UUID for a batch is fabricated.
  const command = useRef<{instruction: string; key: string} | undefined>(undefined);
  const load = async () => {
    try {setBatches((await listScanBatches(p.assignmentId)).items); setError("");}
    catch (e) {setError(scanErrorMessage(e));}
  };
  useEffect(() => {
    const controller = new AbortController();
    listScanBatches(p.assignmentId, controller.signal).then(x => setBatches(x.items)).catch(e => {if (!controller.signal.aborted) setError(scanErrorMessage(e));});
    return () => controller.abort();
  }, [p.assignmentId]);
  const create = async () => {
    const value = instruction.trim();
    if (guard.current || !value) return;
    guard.current = true; setBusy(true); setError("");
    if (command.current?.instruction !== value) command.current = {instruction: value, key: crypto.randomUUID()};
    try {
      const batch = await createScanBatch(p.assignmentId, value, command.current.key);
      p.navigate(`${p.base}/${encodeURIComponent(batch.id)}`);
    } catch (e) {await load(); setError(scanErrorMessage(e));}
    finally {guard.current = false; setBusy(false);}
  };
  return <>
    <section><h2>Партии работ</h2>
      <button className="secondary" disabled={busy} onClick={() => void load()}>Обновить список</button>
      {batches ? batches.length ? <ul className="scan-batches">{batches.map((batch, index) => <li key={batch.id}>
        <a href={`${p.base}/${encodeURIComponent(batch.id)}`} onClick={e => {e.preventDefault(); p.navigate(e.currentTarget.pathname);}}>Партия от {new Date(batch.created_at).toLocaleString("ru-RU")} · {index + 1}</a>
        <span>{batchLabels[batch.status] || "Состояние уточняется"}</span>
      </li>)}</ul> : <p>Партий пока нет. Создайте партию и загрузите сканы работ.</p> : <p role="status">Загрузка партий…</p>}
    </section>
    <form className="assessment-form" onSubmit={e => {e.preventDefault(); void create();}}>
      <h2>Новая партия</h2><label>Инструкция к бумажным работам
        <textarea required maxLength={10000} disabled={busy} value={instruction} onChange={e => setInstruction(e.target.value)} aria-describedby="scan-instruction-help"/>
      </label><p id="scan-instruction-help">Укажите условия работы, например обозначения вариантов на листах. Инструкция сохранится для последующих этапов.</p>
      {error && <p className="alert error" role="alert">{error}</p>}
      <button disabled={busy || !instruction.trim()}>{busy ? "Создание…" : "Создать партию"}</button>
    </form>
  </>;
}
