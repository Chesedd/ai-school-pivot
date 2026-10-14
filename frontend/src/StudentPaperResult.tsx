import {useCallback, useEffect, useRef, useState} from "react";
import {ApiError} from "./api";
import {getAssignmentPaperResult, getStudentPaperPage, type StudentPaperResult as Result} from "./studentPaperApi";
import {ScanPageViewer} from "./ScanPageViewer";
import {errorMessage} from "./errors";
import "./paperReview.css";
import "./paperStudent.css";

const statuses = {correct: "Верно", partially_correct: "Частично верно", incorrect: "Неверно", not_attempted: "Нет ответа"};
export function StudentPaperResult({assignmentId, navigate}: {assignmentId: string; navigate: (to: string) => void}) {
  const [result, setResult] = useState<Result>();
  const [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [pageIndex, setPageIndex] = useState(0), [selected, setSelected] = useState<string>();
  const request = useRef<AbortController | undefined>(undefined);
  const load = useCallback(async () => {
    request.current?.abort();
    const c = new AbortController(); request.current = c;
    // Remove the authoritative snapshot and its image while revalidating.
    setResult(undefined); setSelected(undefined); setPageIndex(0); setLoading(true); setError("");
    try {const value = await getAssignmentPaperResult(assignmentId, c.signal); if (!c.signal.aborted) setResult(value);}
    catch (e) {if (!c.signal.aborted && !(e instanceof ApiError && e.code === "paper_result_not_found")) setError(errorMessage(e, "Не удалось загрузить результат."));}
    finally {if (!c.signal.aborted) setLoading(false);}
  }, [assignmentId]);
  useEffect(() => {void load(); const focus = () => void load(); window.addEventListener("focus", focus); return () => {request.current?.abort(); window.removeEventListener("focus", focus);};}, [load]);
  const paperId = result?.paper_submission_id;
  const loadPage = useCallback((id: string, signal?: AbortSignal) => getStudentPaperPage(paperId!, id, signal), [paperId]);
  const page = result?.pages[pageIndex];
  return <section className="student-paper" aria-label="Результат бумажной работы">
    <div className="page-heading"><h2>Результат бумажной работы</h2><button className="secondary" disabled={loading} onClick={() => void load()}>Обновить результат</button></div>
    {loading ? <p role="status">Загрузка результата…</p> : error ? <p role="alert">{error}</p> : !result ? <p>Результат пока не опубликован.</p> : <>
      <h3>{result.assignment_title}</h3>
      <div className="paper-score"><strong>Балл: {result.result.grade.calculated_total_score} из {result.result.grade.total_max_score}</strong><span>{result.result.grade.calculated_percentage}%</span><strong>Оценка: {result.result.grade.final_grade}</strong></div>
      <p>{result.result.summary}</p>
      <div className="student-paper-grid"><div><h3>Задания</h3>{result.result.items.map((item, i) => <article key={item.assessment_item_id}><h4>Задание {i + 1}</h4><p>{statuses[item.status]} · {item.accepted_score} из {item.max_score}</p>{item.teacher_comment && <p>{item.teacher_comment}</p>}</article>)}
        <h3>Комментарии</h3>{result.result.findings.map(f => <article key={f.finding_id}><button className="secondary" aria-pressed={selected === f.finding_id} onClick={() => {setSelected(f.finding_id); const n = result.pages.findIndex(p => p.scan_page_id === f.scan_page_id); if (n >= 0) setPageIndex(n);}}>{f.short_explanation}</button>{f.detailed_explanation && <p>{f.detailed_explanation}</p>}</article>)}{!result.result.findings.length && <p>Дополнительных комментариев нет.</p>}</div>
        <div><h3>Подтверждённые страницы</h3><nav className="button-row" aria-label="Страницы работы">{result.pages.map((p, i) => <button className="secondary" key={p.scan_page_id} aria-pressed={i === pageIndex} onClick={() => setPageIndex(i)}>Страница {i + 1}</button>)}</nav>
          {page ? <ScanPageViewer key={`${result.id}:${page.scan_page_id}`} page={{id: page.scan_page_id, width_px: page.width, height_px: page.height, content_url: page.content_url, source_artifact_id: "", source_page_index: page.page_order, status: "ready", failure_code: null, content_fingerprint: null}} number={pageIndex + 1} findings={result.result.findings} selectedFinding={selected} onFinding={f => setSelected(f.finding_id)} loadContent={loadPage}/> : <p>Страницы пока недоступны.</p>}
        </div></div>
    </>}
    <button className="secondary" onClick={() => navigate("/student/remediations")}>Дополнительные задания</button>
    <p>Дополнительные задания выполняются отдельно от оценки за работу.</p>
  </section>;
}
