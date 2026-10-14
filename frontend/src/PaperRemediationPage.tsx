import {useCallback, useEffect, useRef, useState} from "react";
import {ApiError, getTeacherAssignment} from "./api";
import {getScanBatch} from "./paperScanApi";
import {readPublicationProgress} from "./paperPublicationApi";
import * as api from "./paperRemediationApi";
import {type Candidate} from "./remediationApi";
import type {PublishedResult} from "./studentPaperApi";
import {errorMessage} from "./errors";
import "./paperStudent.css";

export function PaperRemediationPage(p: {classId: string; assignmentId: string; batchId: string; paperId: string; navigate: (to: string) => void}) {
  const [eligibility, setEligibility] = useState<api.PaperEligibility>();
  const [source, setSource] = useState<{id: string; published_at: string; result: PublishedResult}>();
  const [name, setName] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]), [selected, setSelected] = useState<Candidate[]>([]);
  const [items, setItems] = useState<string[]>([]), [findings, setFindings] = useState<string[]>([]);
  const [title, setTitle] = useState("Дополнительные задания"), [instructions, setInstructions] = useState("");
  const [mode, setMode] = useState<"suggested" | "manual">("suggested"), [q, setQ] = useState("");
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const lock = useRef(false), intent = useRef<api.PaperDraftInput | undefined>(undefined);
  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setSource(undefined); setEligibility(undefined); setCandidates([]); setSearched(false); setSelected([]); setItems([]); setFindings([]);
    try {
      const [assignment, batch] = await Promise.all([getTeacherAssignment(p.assignmentId, signal), getScanBatch(p.batchId, signal)]);
      if (assignment.class_group_id !== p.classId || batch.assignment_id !== p.assignmentId) throw new ApiError(404, "remediation_source_not_found", "");
      const progress = await readPublicationProgress(p.batchId, signal);
      const paper = progress.papers.find(x => x.paper_submission_id === p.paperId);
      if (!paper) throw new ApiError(404, "remediation_source_not_found", "");
      const value = await api.getPaperEligibility(p.paperId, signal);
      const publications = value.active_publication_id ? await api.getPaperSource(p.paperId, signal) : {items: []};
      const current = publications.items.find(x => x.id === value.active_publication_id);
      if (!signal?.aborted) {setName(paper.display_name); setEligibility(value); setSource(current); setItems(value.eligible_assessment_item_ids);}
    } catch (e) {if (!signal?.aborted) setError(errorMessage(e));}
    finally {if (!signal?.aborted) setLoading(false);}
  }, [p.classId, p.assignmentId, p.batchId, p.paperId]);
  useEffect(() => {const c = new AbortController(); void load(c.signal); return () => c.abort();}, [load]);
  const signals = (): api.PaperSignals => ({source_paper_publication_id: source!.id, source_assessment_item_ids: [...items], publication_finding_ids: [...findings]});
  const act = async (operation: () => Promise<void>) => {
    if (lock.current) return; lock.current = true; setBusy(true); setError("");
    try {await operation();} catch (e) {
      setError(errorMessage(e));
      if (e instanceof ApiError && e.status < 500) intent.current = undefined;
      if (e instanceof ApiError && e.code === "remediation_source_publication_stale") {intent.current = undefined; await load();}
    } finally {lock.current = false; setBusy(false);}
  };
  const create = () => act(async () => {
    // Preserve exact source/body/key across an uncertain response.
    intent.current ??= {...signals(), creation_key: crypto.randomUUID(), title: title.trim(), instructions: instructions.trim() || null, items: selected.map(c => ({task_version_id: c.task_version_id, selection_source: mode}))};
    const plan = await api.createPaperDraft(p.paperId, intent.current);
    intent.current = undefined;
    p.navigate(`/classrooms/${plan.class_group_id}/students/${plan.student_id}/remediations/${plan.id}`);
  });
  return <section className="paper-remediation"><button className="secondary" onClick={() => p.navigate(`/classrooms/${p.classId}/assignments/${p.assignmentId}/paper/${p.batchId}/publications`)}>← К публикациям</button><h1>Дополнительные задания</h1><p>{name}</p>{error && <p role="alert">{error}</p>}
    {loading ? <p role="status">Загрузка источника…</p> : !eligibility ? <button onClick={() => void load()}>Повторить</button> : <>
      <p>{eligibility.eligible ? "Можно назначить дополнительные задания." : eligibility.reason === "no_active_publication" ? "Результат пока не опубликован." : "Нет заданий для отработки."}</p>
      {source && <><p>Результат от {new Date(source.published_at).toLocaleString("ru-RU")}</p><h2>Слабые задания и комментарии</h2>{source.result.items.map((item, i) => eligibility.eligible_assessment_item_ids.includes(item.assessment_item_id) && <label key={item.assessment_item_id}><input type="checkbox" disabled={busy || !!intent.current} checked={items.includes(item.assessment_item_id)} onChange={() => {setItems(old => old.includes(item.assessment_item_id) ? old.filter(x => x !== item.assessment_item_id) : [...old, item.assessment_item_id]); setCandidates([]); setSelected([]);}}/>Задание {i + 1}: {item.accepted_score} из {item.max_score}{item.teacher_comment && <p>{item.teacher_comment}</p>}</label>)}
        {source.result.findings.map(f => <label key={f.finding_id}><input type="checkbox" disabled={busy || !!intent.current} checked={findings.includes(f.finding_id)} onChange={() => {setFindings(old => old.includes(f.finding_id) ? old.filter(x => x !== f.finding_id) : [...old, f.finding_id]); setCandidates([]); setSelected([]);}}/>{f.short_explanation}</label>)}
        {eligibility.eligible && <><h2>Подбор заданий</h2><label>Режим<select disabled={busy || !!intent.current} value={mode} onChange={e => {setMode(e.target.value as "suggested" | "manual"); setCandidates([]); setSelected([]);}}><option value="suggested">По рекомендации</option><option value="manual">Вручную</option></select></label>{mode === "manual" && <label>Поиск<input disabled={busy || !!intent.current} value={q} onChange={e => setQ(e.target.value)}/></label>}<button disabled={busy || !items.length || !!intent.current} onClick={() => void act(async () => {setCandidates(await api.searchPaperCandidates(p.paperId, signals(), mode, q)); setSearched(true);})}>Подобрать задания</button>
          {searched && !candidates.length && <p>Подходящих готовых заданий не найдено. Попробуйте ручной поиск.</p>}{candidates.map(c => <article key={c.task_version_id}><h3>{c.title || "Задание"}</h3><p>{c.statement_preview}</p>{c.primary_skill_name && <p>Навык: {c.primary_skill_name}</p>}{c.reasons.map((r, i) => <p key={i}>{r.matched_title || r.match_type}</p>)}<button disabled={busy || selected.length >= 20 || selected.some(x => x.task_version_id === c.task_version_id) || !!intent.current} onClick={() => setSelected(old => [...old, c])}>Добавить</button></article>)}
          <h3>Выбрано: {selected.length} из 20</h3>{selected.map(c => <p key={c.task_version_id}>{c.title || "Задание"} <button disabled={busy || !!intent.current} onClick={() => setSelected(old => old.filter(x => x !== c))}>Удалить</button></p>)}
          <label>Название<input maxLength={200} disabled={busy || !!intent.current} value={title} onChange={e => setTitle(e.target.value)}/></label><label>Комментарий ученику<textarea maxLength={4000} disabled={busy || !!intent.current} value={instructions} onChange={e => setInstructions(e.target.value)}/></label><button disabled={busy || !title.trim() || !items.length || !selected.length} onClick={() => void create()}>{intent.current ? "Повторить создание черновика" : "Создать дополнительные задания"}</button>
        </>}
      </>}
      {eligibility.eligible && !source && <p role="alert">Результат изменился. Обновите данные. <button onClick={()=>void load()}>Обновить</button></p>}
    </>}
  </section>;
}
