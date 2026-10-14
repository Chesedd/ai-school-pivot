import {useEffect, useState} from "react";
import type {Remediation} from "./remediationApi";
import {getPaperSource} from "./paperRemediationApi";
import type {PublishedResult} from "./studentPaperApi";
import {errorMessage} from "./errors";

// Read only the plan's immutable publication, including after replacement.
export function PaperPlanSource({plan}: {plan: Remediation}) {
  const [source, setSource] = useState<{published_at: string; result: PublishedResult}>(), [error, setError] = useState("");
  useEffect(() => {
    const c = new AbortController(); setSource(undefined); setError("");
    getPaperSource(plan.source_paper_submission_id!, c.signal).then(x => {
      if (!c.signal.aborted) {const value = x.items.find(p => p.id === plan.source_paper_publication_id); setSource(value); if (!value) setError("Источник дополнительных заданий недоступен.");}
    }).catch(e => {if (!c.signal.aborted) setError(errorMessage(e));});
    return () => c.abort();
  }, [plan.source_paper_submission_id, plan.source_paper_publication_id]);
  return <section><h2>Источник дополнительных заданий</h2>{error ? <p role="alert">{error}</p> : source ? <>
    <p>Результат от {new Date(source.published_at).toLocaleString("ru-RU")}</p>
    {source.result.items.map((item, i) => plan.source_assessment_item_ids?.includes(item.assessment_item_id) && <p key={item.assessment_item_id}>Задание {i + 1}: {item.accepted_score} из {item.max_score}{item.teacher_comment && ` · ${item.teacher_comment}`}</p>)}
    {source.result.findings.filter(f => plan.publication_finding_ids?.includes(f.finding_id)).map(f => <p key={f.finding_id}>{f.short_explanation}</p>)}
  </> : <p role="status">Загрузка источника…</p>}</section>;
}
