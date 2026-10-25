import {useCallback, useEffect, useRef, useState} from "react";
import {ApiError, getAssessment, getTeacherAssignment, type AssessmentVariant} from "./api";
import {freezeGradingPolicy, latestPolicies, readGradingPolicies, type FrozenPolicy, type GradingPolicy} from "./paperGradingApi";
import type {ScanBatch} from "./paperScanApi";

const messages: Record<string, string> = {
  paper_grading_policy_locked: "Проверка уже началась. Оценивание этой партии менять нельзя.",
  paper_grading_policy_item_coverage: "Состав заданий изменился. Обновите данные и откройте настройку заново.",
  paper_grading_policy_instruction_mismatch: "Инструкция партии изменилась. Обновите страницу.",
  paper_grading_policy_version_mismatch: "Обновите приложение перед настройкой оценивания.",
  foreign_assessment_variant: "Вариант не относится к этой работе.", variant_not_represented: "В партии нет работ этого варианта.",
};
function errorText(e: unknown) {
  if (!(e instanceof ApiError)) return "Не удалось сохранить оценивание. Проверьте соединение и повторите попытку.";
  if ([401, 403, 404].includes(e.status)) return "Нет доступа к настройке оценивания. Обратитесь к учителю класса.";
  if (e.code === "validation_error") {
    const fields = Array.isArray(e.details) ? e.details.flatMap(issue => {
      const field = typeof issue?.field === "string" ? issue.field : "";
      const item = /^assessment_items\.(\d+)\.(rubric|partial_credit_policy)/.exec(field);
      if (item) return [`Задание ${Number(item[1]) + 1}: проверьте ${item[2] === "rubric" ? "критерии" : "частичный балл"}.`];
      return field.startsWith("grade_scale") ? ["Проверьте шкалу оценки."] : [];
    }) : [];
    return `Проверьте критерии и шкалу: пороги должны убывать до нуля, а названия оценок не должны повторяться. ${[...new Set(fields)].join(" ")}`.trim();
  }
  return messages[e.code] || "Не удалось сохранить оценивание. Обновите данные и повторите попытку.";
}

export function PaperGradingSetup({batch, variantIds, status, onReady, onSaved}: {
  batch: ScanBatch; variantIds: string[]; status: string; onReady: (ready: boolean) => void; onSaved: () => Promise<void>;
}) {
  const [policies, setPolicies] = useState<Map<string, FrozenPolicy>>();
  const [variants, setVariants] = useState<AssessmentVariant[]>([]);
  const [editing, setEditing] = useState<string>();
  const [draft, setDraft] = useState<GradingPolicy>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [denied, setDenied] = useState(false);
  const lock = useRef(false);
  const first = useRef<HTMLTextAreaElement>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const idsKey = [...new Set(variantIds)].sort().join(",");
  const ready = useCallback((rows: Map<string, FrozenPolicy>) => !!idsKey && idsKey.split(",").every(id => rows.has(id)), [idsKey]);
  const reload = async () => {
    const rows = latestPolicies((await readGradingPolicies(batch.id)).items);
    setPolicies(rows); return rows;
  };
  useEffect(() => {
    const controller = new AbortController();
    setPolicies(undefined); setEditing(undefined); setDraft(undefined); setError(""); setDenied(false); onReady(false);
    void readGradingPolicies(batch.id, controller.signal).then(result => {
      if (!controller.signal.aborted) {const rows = latestPolicies(result.items); setPolicies(rows);}
    }).catch(e => {if (!controller.signal.aborted) {setError(errorText(e)); setDenied(e instanceof ApiError && [401, 403, 404].includes(e.status));}});
    return () => controller.abort();
  }, [batch.id, ready, onReady]);
  useEffect(() => {onReady(!!policies && ready(policies) && !draft && !busy && !denied);}, [policies, ready, draft, busy, denied, onReady]);
  useEffect(() => {if (draft) first.current?.focus();}, [editing, !!draft]);
  const close = () => {setEditing(undefined); setDraft(undefined); queueMicrotask(() => opener.current?.focus());};
  const open = async (id: string, button: HTMLButtonElement) => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(""); setNotice(""); opener.current = button;
    try {
      let available = variants;
      if (!available.length) {const assignment = await getTeacherAssignment(batch.assignment_id); available = (await getAssessment(assignment.assessment_id)).variants; setVariants(available);}
      const rows = await reload(); const variant = available.find(v => v.id === id);
      if (!variant) throw new Error("missing variant");
      setEditing(id);
      const value = structuredClone(rows.get(id)?.policy || {
        schema_version: "paper_grading_policy.v1", original_teacher_instruction: batch.instruction,
        assessment_items: variant.items.map(item => ({assessment_item_id: item.id, max_score: item.points,
          rubric_source: "teacher_override" as const, rubric: {text: ""}, partial_credit_policy: "", special_rules: []})),
        total_max_score: variant.total_points, general_rules: [], grade_scale: {basis: "percent" as const, thresholds: [{minimum: "0", grade_label: ""}]},
        additional_context: null, compiler_version: "1.0.0", prompt_policy_version: "1.0.0",
      });
      // Frozen policies canonicalize by UUID, while teachers use task order.
      const positions = new Map(variant.items.map(item => [item.id, item.position]));
      value.assessment_items.sort((a, b) => (positions.get(a.assessment_item_id) || 0) - (positions.get(b.assessment_item_id) || 0));
      setDraft(value);
    } catch (e) {setError(errorText(e)); if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {setDenied(true); onReady(false);}}
    finally {lock.current = false; setBusy(false);}
  };
  const save = async () => {
    if (!draft || !editing || lock.current) return;
    const thresholds = draft.grade_scale.thresholds;
    const minima = thresholds.map(t => Number(t.minimum));
    if (minima.some((v, i) => !Number.isFinite(v) || v < 0 || (i > 0 && v >= minima[i - 1]) || v > (draft.grade_scale.basis === "percent" ? 100 : Number(draft.total_max_score))) || minima.at(-1) !== 0 || new Set(thresholds.map(t => t.grade_label.trim())).size !== thresholds.length) {
      setError("Пороги должны строго убывать до нуля и не превышать максимум. Названия оценок должны быть разными."); return;
    }
    lock.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const previous = policies?.get(editing)?.id;
      const rows = await reload();
      if (rows.get(editing)?.id !== previous) {
        close(); setNotice("Другой учитель уже настроил оценивание. Загружена актуальная версия."); await onSaved(); return;
      }
      await freezeGradingPolicy(batch.id, editing, {...draft, assessment_items: draft.assessment_items.map(item => ({...item, rubric: {...item.rubric, text: item.rubric.text.trim()}, partial_credit_policy: item.partial_credit_policy.trim()})), grade_scale: {...draft.grade_scale, thresholds: thresholds.map(t => ({...t, grade_label: t.grade_label.trim()}))}});
      await reload(); await onSaved(); close(); setNotice("Оценивание сохранено.");
    } catch (e) {
      setError(errorText(e));
      if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {setDenied(true); onReady(false); close();}
      else {try {await reload(); await onSaved();} catch {onReady(false);}}
    } finally {lock.current = false; setBusy(false);}
  };
  const editable = status === "ready_for_checking" && !denied;
  return <section className="checking-summary grading-setup" aria-label="Оценивание">
    <h3>Оценивание</h3>
    {!policies && !error && <p role="status">Загрузка оценивания…</p>}
    {policies && idsKey.split(",").filter(Boolean).some(id => !policies.has(id)) && <p>Перед проверкой настройте критерии и шкалу оценки для каждого варианта.</p>}
    {error && !draft && <p id="grading-error" role="alert" className="alert error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!policies && error && !denied && <button type="button" onClick={() => void reload().then(() => setError("")).catch(e => setError(errorText(e)))}>Обновить оценивание</button>}
    {policies && idsKey.split(",").filter(Boolean).map((id, index) => {
      const policy = policies.get(id)?.policy;
      return <div key={id}><p><strong>{variants.find(v => v.id === id)?.name || `Вариант ${index + 1}`}</strong>{policy ? ` · Максимум: ${policy.total_max_score} · ${policy.grade_scale.thresholds.map(t => `${t.grade_label}: от ${t.minimum}${policy.grade_scale.basis === "percent" ? "%" : " б."}`).join("; ")}` : " · Оценивание не настроено"}</p>
        {editable && <button type="button" className="secondary" disabled={busy || !!draft} onClick={e => void open(id, e.currentTarget)}>{policy ? "Изменить оценивание" : "Настроить оценивание"}</button>}
      </div>;
    })}
    {status !== "ready_for_checking" && policies && <p>После начала проверки оценивание зафиксировано.</p>}
    {draft && editing && <form className="grading-form" aria-label="Настройка оценивания" aria-busy={busy} aria-describedby={error ? "grading-error" : undefined} onSubmit={e => {e.preventDefault(); void save();}}>
      <h4>{variants.find(v => v.id === editing)?.name}</h4><p>Максимальные баллы взяты из работы. Инструкция: {batch.instruction}</p>
      <fieldset disabled={busy}><legend>Критерии заданий</legend>{draft.assessment_items.map((item, index) => <div key={item.assessment_item_id}>
        <p>Задание {index + 1} · Максимум: {item.max_score}</p>
        <label>Критерии задания {index + 1}<textarea ref={index === 0 ? first : undefined} required maxLength={30000} value={item.rubric.text} onChange={e => setDraft({...draft, assessment_items: draft.assessment_items.map((x, i) => i === index ? {...x, rubric_source: "teacher_override", rubric: {text: e.target.value.trimStart()}} : x)})}/></label>
        <label>Частичный балл задания {index + 1}<textarea required maxLength={2000} value={item.partial_credit_policy} onChange={e => setDraft({...draft, assessment_items: draft.assessment_items.map((x, i) => i === index ? {...x, partial_credit_policy: e.target.value.trimStart()} : x)})}/></label>
      </div>)}</fieldset>
      <fieldset disabled={busy}><legend>Шкала оценки</legend>
        <label>Пороги оценки<select value={draft.grade_scale.basis} onChange={e => setDraft({...draft, grade_scale: {...draft.grade_scale, basis: e.target.value as "percent" | "points"}})}><option value="percent">Проценты</option><option value="points">Баллы</option></select></label>
        <p>Укажите пороги от большего к меньшему. Последний порог — 0.</p>
        {draft.grade_scale.thresholds.map((threshold, index) => <div key={index}>
          <label>Минимум для оценки {index + 1}<input type="number" required min="0" max={draft.grade_scale.basis === "percent" ? "100" : draft.total_max_score} step="any" value={threshold.minimum} onChange={e => setDraft({...draft, grade_scale: {...draft.grade_scale, thresholds: draft.grade_scale.thresholds.map((x, i) => i === index ? {...x, minimum: e.target.value} : x)}})}/></label>
          <label>Название оценки {index + 1}<input required maxLength={64} value={threshold.grade_label} onChange={e => setDraft({...draft, grade_scale: {...draft.grade_scale, thresholds: draft.grade_scale.thresholds.map((x, i) => i === index ? {...x, grade_label: e.target.value.trimStart()} : x)}})}/></label>
          {draft.grade_scale.thresholds.length > 1 && <button type="button" className="secondary" onClick={() => setDraft({...draft, grade_scale: {...draft.grade_scale, thresholds: draft.grade_scale.thresholds.filter((_, i) => i !== index)}})}>Удалить порог {index + 1}</button>}
        </div>)}
        <button type="button" className="secondary" disabled={draft.grade_scale.thresholds.length >= 64} onClick={() => setDraft({...draft, grade_scale: {...draft.grade_scale, thresholds: [...draft.grade_scale.thresholds, {minimum: "0", grade_label: ""}]}})}>Добавить порог</button>
      </fieldset>
      {error && <p id="grading-error" role="alert" className="alert error">{error}</p>}
      <div className="button-row"><button disabled={busy} type="submit">{busy ? "Сохраняем оценивание…" : "Сохранить оценивание"}</button><button type="button" className="secondary" disabled={busy} onClick={close}>Отмена</button></div>
    </form>}
  </section>;
}
