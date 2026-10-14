import {useEffect, useRef, useState} from "react";
import {ApiError} from "./api";
import * as api from "./paperReviewApi";
import {approvalError, itemApprovalError, itemStatusLabels} from "./paperReviewState";
import {scanErrorMessage} from "./paperScanState";

export function PaperReviewPanel(p: {
  paper: api.EffectivePaper; pageIds: string[]; pageId: string; selectedItem: string;
  selectedFinding: string; point: api.FindingRegion | null;
  onSelectItem: (id: string) => void; onFinding: (f: api.ReviewFinding) => void;
  onEvidence: (findings: api.ReviewFinding[]) => void; onPickPoint: (enabled: boolean) => void;
  onDirtyChange: (dirty: boolean) => void; refreshProgress: () => Promise<void>;
}) {
  const [review, setReview] = useState<api.PaperReview>();
  const [draft, setDraft] = useState<api.ReviewDraft>();
  const [suggestions, setSuggestions] = useState<api.AISuggestion[]>([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [correction, setCorrection] = useState(false);
  const [adding, setAdding] = useState(false);
  const [findingText, setFindingText] = useState("");
  const [editingFinding, setEditingFinding] = useState<string | null>(null);
  const [overrideGrade, setOverrideGrade] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const locked = useRef(false);
  const mounted = useRef(true);
  const paper = useRef(p.paper); paper.current = p.paper;
  const dirty = !!review && !!draft && JSON.stringify(draft) !== JSON.stringify(review.draft);
  const hasLocal = dirty || adding || !!overrideGrade || !!overrideReason;
  const readOnly = review?.state === "approved" && !correction;
  const install = (value: api.PaperReview) => {
    if (!mounted.current) return;
    setReview(value); setDraft(value.draft); setCorrection(false); setAdding(false); setEditingFinding(null);
    setOverrideGrade(""); setOverrideReason(""); p.onPickPoint(false);
  };
  useEffect(() => {
    mounted.current = true;
    return () => {mounted.current = false;};
  }, []);
  useEffect(() => {
    // Observe a shared review appearing or changing lifecycle in the projection.
    // Preserve local edits; stale writers are resolved by CAS, never polling.
    if (!p.paper.review_id || hasLocal || locked.current || review && review.review_id === p.paper.review_id && review.state === p.paper.review_state && review.latest_approved_revision_id === p.paper.latest_approved_revision_id) return;
    const controller = new AbortController();
    api.readReview(p.paper, controller.signal).then(v => {if (!controller.signal.aborted) install(v);}).catch(e => {if (!controller.signal.aborted) setError(scanErrorMessage(e));});
    return () => controller.abort();
  }, [p.paper.review_id, p.paper.review_state, p.paper.latest_approved_revision_id]);
  useEffect(() => {
    if (!p.paper.effective_check_run_id || !p.paper.ai_review_available) return;
    const controller = new AbortController();
    api.readAISuggestions(p.paper, controller.signal).then(v => {if (!controller.signal.aborted) setSuggestions(v.items);}).catch(e => {if (!controller.signal.aborted) setError(scanErrorMessage(e));});
    return () => controller.abort();
  }, [p.paper.effective_check_run_id, p.paper.ai_review_available]);
  useEffect(() => {p.onDirtyChange(hasLocal); return () => p.onDirtyChange(false);}, [hasLocal, p.onDirtyChange]);
  useEffect(() => {
    const before = (e: BeforeUnloadEvent) => {if (hasLocal) {e.preventDefault(); e.returnValue = "";}};
    window.addEventListener("beforeunload", before); return () => window.removeEventListener("beforeunload", before);
  }, [hasLocal]);
  useEffect(() => {p.onEvidence(draft?.findings || []);}, [draft, p.onEvidence]);
  const mutate = async (fn: () => Promise<api.PaperReview>, success: string) => {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(""); setNotice("");
    try {install(await fn()); if (mounted.current) setNotice(success);}
    catch (e) {
      if (mounted.current && e instanceof ApiError && e.code === "paper_review_version_conflict") {
        // Discard stale edits only after an authoritative read succeeds.
        try {install(await api.readReview(paper.current)); setNotice("Проверка была изменена другим учителем. Загружена актуальная версия.");}
        catch (readError) {setError(scanErrorMessage(readError));}
      } else if (mounted.current) {
        setError(scanErrorMessage(e));
        if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {setReview(undefined); setDraft(undefined);}
      }
    } finally {
      await p.refreshProgress();
      if (mounted.current) setBusy(false);
      locked.current = false;
    }
  };
  const updateItem = (id: string, patch: Partial<api.ReviewItem>) => setDraft(d => d && ({...d, items: d.items.map(i => i.assessment_item_id === id ? {...i, ...patch} : i)}));
  const updateFinding = (id: string, patch: Partial<api.ReviewFinding>) => setDraft(d => d && ({...d, findings: d.findings.map(f => f.finding_id === id ? {...f, ...patch} : f)}));
  const removeFinding = (id: string) => setDraft(d => d && ({...d, findings: d.findings.filter(f => f.finding_id !== id)}));
  const addFinding = () => {
    if (!draft || !p.point || !p.pageIds.includes(p.pageId) || !findingText.trim() || !draft.items.some(i => i.assessment_item_id === p.selectedItem)) return;
    const f: api.ReviewFinding = {finding_id: crypto.randomUUID(), assessment_item_id: p.selectedItem, source_check_finding_id: null,
      scan_page_id: p.pageId, category: "teacher_note", severity: "info", short_explanation: findingText.trim(), detailed_explanation: null,
      region: p.point, coordinate_space_version: "normalized_upright_v1"};
    const existing = editingFinding && draft.findings.find(x => x.finding_id === editingFinding && !x.source_check_finding_id);
    const next = existing ? {...existing, assessment_item_id: p.selectedItem, scan_page_id: p.pageId, region: p.point, short_explanation: findingText.trim()} : f;
    setDraft({...draft, findings: existing ? draft.findings.map(x => x.finding_id === existing.finding_id ? next : x) : [...draft.findings, next]});
    setAdding(false); setEditingFinding(null); setFindingText(""); p.onPickPoint(false); p.onFinding(next);
  };
  const grade = review?.latest_approved_revision?.grade_result;
  const reason = draft ? dirty ? "Сначала сохраните изменения." : approvalError(draft) || (adding ? "Завершите добавление замечания." : "") || (overrideGrade.trim() && !overrideReason.trim() ? "Укажите причину изменения оценки." : "") : "";
  return <section className="review-editor" aria-label="Задания и решения учителя">
    <h2>Задания</h2>
    {notice && <p role="status" className="matching-notice">{notice}</p>}
    {error && <p role="alert" className="alert error" id="review-error">{error}</p>}
    {!draft ? <>
      {p.paper.review_id ? <><p role="status">{error ? "Проверка недоступна." : "Загрузка проверки…"}</p>{error && <button className="secondary" disabled={busy} onClick={() => void mutate(() => api.readReview(p.paper), "Загружена актуальная версия.")}>Обновить проверку</button>}</> : <>
        <p>{p.paper.ai_review_available ? "Результат готов. Откройте проверку и проверьте решения." : "Решения появятся после проверки работы."}</p>
        {p.paper.ai_review_available && p.paper.effective_check_run_id && <button disabled={busy} onClick={() => void mutate(() => api.openReview(p.paper), "Проверка открыта.")}>Открыть проверку</button>}
        {p.paper.manual_review_available && <button className="secondary" disabled={busy} onClick={() => void mutate(() => api.openReview({...p.paper, effective_check_run_id: null}), "Ручная проверка открыта.")}>Проверить вручную</button>}
      </>}
    </> : <>
      <p className="review-provenance">{draft.source_kind === "manual" ? "Ручная проверка" : "Проверка с предложениями ИИ"} · {readOnly ? "Подтверждено" : "Черновик"}{hasLocal && " · Есть несохранённые изменения"}</p>
      {review?.latest_approved_revision && <div className="review-approved" aria-label="Подтверждённый результат">
        <strong>Подтверждённая версия {review.latest_approved_revision.revision_number}</strong>
        {!readOnly && <p>Новый черновик не подтверждён. Предыдущая подтверждённая версия сохранена.</p>}
        {grade && <><p>Баллы: {grade.calculated_total_score} / {grade.total_max_score} · {grade.calculated_percentage}%</p><p>Расчётная оценка: {grade.calculated_grade} · Итоговая оценка: {grade.final_grade}</p>{grade.grade_override_reason && <p>Причина изменения: {grade.grade_override_reason}</p>}</>}
        {!readOnly && <details><summary>Решения подтверждённой версии</summary>{review.latest_approved_revision.snapshot.items.map((i, n) => <p key={i.assessment_item_id}>Задание {n + 1}: {i.status ? itemStatusLabels[i.status] : "Не оценено"} · {i.accepted_score ?? "—"} / {i.max_score}</p>)}</details>}
        {readOnly && <button className="secondary" onClick={() => setCorrection(true)}>Изменить проверку</button>}
      </div>}
      {draft.items.map((item, n) => {
        const suggestion = suggestions.find(s => s.assessment_item_id === item.assessment_item_id);
        const id = `review-item-${item.assessment_item_id}`;
        const validation = itemApprovalError(item);
        return <article key={item.assessment_item_id} id={id} className={`review-item ${p.selectedItem === item.assessment_item_id ? "selected" : ""}`}>
          <button className="link-button" aria-pressed={p.selectedItem === item.assessment_item_id} onClick={() => p.onSelectItem(item.assessment_item_id)}><strong>Задание {n + 1}</strong></button>
          <p>{item.status ? itemStatusLabels[item.status] : "Не оценено"} · {item.accepted_score ?? "—"} / {item.max_score}</p>
          {draft.source_kind === "ai" && suggestion && <p className="review-suggestion">Предложено: {itemStatusLabels[suggestion.status]} · {suggestion.suggested_score ?? "—"} / {suggestion.max_score}</p>}
          <label>Решение учителя<select disabled={busy || readOnly} value={item.status ?? ""} aria-describedby={validation ? `${id}-error` : undefined} onChange={e => updateItem(item.assessment_item_id, {status: e.target.value as api.ItemStatus || null})}>
            {draft.source_kind === "manual" && <option value="">Не оценено</option>}{Object.entries(itemStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>
          <label>Баллы · максимум {item.max_score}<input type="number" min="0" max={item.max_score} step="any" disabled={busy || readOnly} value={item.accepted_score ?? ""} aria-describedby={validation ? `${id}-error` : undefined} aria-invalid={item.accepted_score !== null && (Number(item.accepted_score) < 0 || Number(item.accepted_score) > Number(item.max_score))} onChange={e => updateItem(item.assessment_item_id, {accepted_score: e.target.value === "" ? null : e.target.value})}/></label>
          {validation && !readOnly && <small id={`${id}-error`}>{validation}</small>}
          <label>Комментарий к заданию<textarea maxLength={2000} disabled={busy || readOnly} value={item.teacher_comment ?? ""} onChange={e => updateItem(item.assessment_item_id, {teacher_comment: e.target.value || null})}/></label>
          <ul className="review-findings">{draft.findings.filter(f => f.assessment_item_id === item.assessment_item_id).map(f => <li key={f.finding_id}>
            <button className={`secondary ${p.selectedFinding === f.finding_id ? "selected" : ""}`} aria-pressed={p.selectedFinding === f.finding_id} onClick={() => p.onFinding(f)}>{f.short_explanation}</button>
            <small>{f.source_check_finding_id ? "Замечание ИИ" : "Замечание учителя"} · Страница {p.pageIds.indexOf(f.scan_page_id) + 1}</small>
            {f.detailed_explanation && <p>{f.detailed_explanation}</p>}
            {!readOnly && !f.source_check_finding_id && <>
              <label>Текст замечания<input required maxLength={500} disabled={busy} value={f.short_explanation} onChange={e => updateFinding(f.finding_id, {short_explanation: e.target.value})}/></label>
              <label>Подробный комментарий<textarea maxLength={4000} disabled={busy} value={f.detailed_explanation ?? ""} onChange={e => updateFinding(f.finding_id, {detailed_explanation: e.target.value || null})}/></label>
              <label>Важность замечания<select disabled={busy} value={f.severity} onChange={e => updateFinding(f.finding_id, {severity: e.target.value as api.ReviewFinding["severity"]})}><option value="info">Комментарий</option><option value="warning">Требует внимания</option><option value="error">Ошибка</option></select></label>
              <label>Задание замечания<select disabled={busy || adding} value={f.assessment_item_id ?? ""} onChange={e => updateFinding(f.finding_id, {assessment_item_id: e.target.value})}>{draft.items.map((i, index) => <option key={i.assessment_item_id} value={i.assessment_item_id}>Задание {index + 1}</option>)}</select></label>
              <button className="secondary" disabled={busy || adding} onClick={() => {p.onFinding(f); setEditingFinding(f.finding_id); setFindingText(f.short_explanation); setAdding(true); p.onPickPoint(true);}}>Изменить место замечания</button>
            </>}
            {!readOnly && <button className="link-button" disabled={busy} onClick={() => removeFinding(f.finding_id)}>Убрать замечание из проверки</button>}
          </li>)}</ul>
          {!readOnly && !adding && <button className="secondary" disabled={busy} onClick={() => {p.onSelectItem(item.assessment_item_id); setEditingFinding(null); setFindingText(""); setAdding(true); p.onPickPoint(true);}}>Добавить замечание</button>}
        </article>;
      })}
      {draft.findings.some(f => !f.assessment_item_id) && <div className="review-legacy"><h3>Замечания без связи с заданием</h3>{draft.findings.filter(f => !f.assessment_item_id).map(f => <div key={f.finding_id}><button className="secondary" onClick={() => p.onFinding(f)}>{f.short_explanation}</button><label>Задание<select disabled={busy || readOnly} value="" onChange={e => updateFinding(f.finding_id, {assessment_item_id: e.target.value})}><option value="">Выберите задание</option>{draft.items.map((i, n) => <option key={i.assessment_item_id} value={i.assessment_item_id}>Задание {n + 1}</option>)}</select></label>{!readOnly && <button className="link-button" onClick={() => removeFinding(f.finding_id)}>Убрать замечание из проверки</button>}</div>)}</div>}
      {adding && <div className="review-add-finding"><h3>{editingFinding ? "Изменение замечания" : "Новое замечание"}</h3><p>Выберите место в просмотре текущей страницы.</p>
        <label>Текст замечания<textarea autoFocus required maxLength={500} value={findingText} onChange={e => setFindingText(e.target.value)}/></label>
        <p role="status">{p.point ? "Место на странице выбрано." : "Сначала выберите место на странице."}</p>
        <div className="button-row"><button disabled={busy || !p.point || !findingText.trim()} onClick={addFinding}>{editingFinding ? "Применить место" : "Добавить"}</button><button className="secondary" onClick={() => {setAdding(false); setEditingFinding(null); setFindingText(""); p.onPickPoint(false);}}>Отмена</button></div>
      </div>}
      <label>Итоговый комментарий<textarea maxLength={4000} disabled={busy || readOnly} value={draft.summary} onChange={e => setDraft({...draft, summary: e.target.value})}/></label>
      {!readOnly && <><fieldset className="review-override"><legend>Изменение итоговой оценки (необязательно)</legend><label>Итоговая оценка<input maxLength={64} disabled={busy} value={overrideGrade} onChange={e => setOverrideGrade(e.target.value)}/></label><label>Причина изменения<input maxLength={2000} required={!!overrideGrade.trim()} disabled={busy} value={overrideReason} onChange={e => setOverrideReason(e.target.value)}/></label></fieldset>
        <footer className="review-actions"><p role="status" id="approval-reason">{busy ? "Сохраняем…" : reason || "Можно подтвердить проверку."}</p><div className="button-row">
          <button className="secondary" disabled={busy || !dirty || adding || draft.items.some(i => i.accepted_score !== null && (Number(i.accepted_score) < 0 || Number(i.accepted_score) > Number(i.max_score))) || draft.findings.some(f => !f.short_explanation.trim())} onClick={() => void mutate(() => api.saveReview(paper.current, review!.version, draft), "Черновик сохранён.")}>Сохранить</button>
          <button disabled={busy || !!reason} aria-describedby="approval-reason" onClick={() => void mutate(() => api.approveReview(paper.current, review!.version, overrideGrade.trim() ? {override_grade: overrideGrade.trim(), reason: overrideReason.trim()} : null), "Проверка подтверждена.")}>Подтвердить проверку</button>
        </div></footer>
      </>}
    </>}
  </section>;
}
