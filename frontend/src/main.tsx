import { FormEvent, StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Item = { id: string; name: string; subject_id?: string; grade_id?: string; topic_id?: string; subtopic_id?: string };
type Catalogs = Record<"subjects" | "grades" | "topics" | "subtopics" | "skills", Item[]>;
const api = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const formats: Record<string, string[]> = { test: ["single_choice", "multiple_choice"], calculation: ["short_text", "number", "expression"], problem: ["number", "expression", "long_text"], open_question: ["short_text", "long_text"], essay: ["long_text"] };

function App() {
  const [catalogs, setCatalogs] = useState<Catalogs>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState<any>();
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ subject_id: "", grade_id: "", topic_id: "", subtopic_id: "", skill_id: "", title: "", statement: "", task_type: "calculation", answer_format: "number", difficulty: "basic", source: "" });
  useEffect(() => { Promise.all((["subjects", "grades", "topics", "subtopics", "skills"] as const).map(async name => [name, (await (await fetch(`${api}/api/content-bank/catalog/${name}`)).json()).items] as const)).then(rows => setCatalogs(Object.fromEntries(rows) as Catalogs)).catch(() => setError("Не удалось загрузить справочники.")).finally(() => setLoading(false)); }, []);
  const topics = useMemo(() => catalogs?.topics.filter(x => x.subject_id === form.subject_id && x.grade_id === form.grade_id) ?? [], [catalogs, form.subject_id, form.grade_id]);
  const subtopics = catalogs?.subtopics.filter(x => x.topic_id === form.topic_id) ?? [];
  const skills = catalogs?.skills.filter(x => x.topic_id === form.topic_id && (!form.subtopic_id || x.subtopic_id === form.subtopic_id)) ?? [];
  const change = (name: string, value: string) => setForm(old => ({ ...old, [name]: value, ...(name === "subject_id" || name === "grade_id" ? { topic_id: "", subtopic_id: "", skill_id: "" } : {}), ...(name === "topic_id" ? { subtopic_id: "", skill_id: "" } : {}), ...(name === "subtopic_id" ? { skill_id: "" } : {}), ...(name === "task_type" ? { answer_format: formats[value][0] } : {}) }));
  async function submit(event: FormEvent) { event.preventDefault(); setSaving(true); setError(""); setSuccess(undefined); const body = { subject_id: form.subject_id, grade_id: form.grade_id, topic_id: form.topic_id, subtopic_id: form.subtopic_id || null, initial_version: { title: form.title || null, statement: form.statement, task_type: form.task_type, answer_format: form.answer_format, difficulty: form.difficulty, source: form.source || null, skills: [{ skill_id: form.skill_id, weight: "1.0000", is_primary: true }] } }; try { const response = await fetch(`${api}/api/content-bank/tasks`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); const data = await response.json(); if (!response.ok) throw new Error(data.error?.details?.map((x: any) => `${x.field}: ${x.message}`).join("; ") || "Ошибка создания задания."); setSuccess(data); } catch (e) { setError(e instanceof Error ? e.message : "Ошибка создания задания."); } finally { setSaving(false); } }
  if (location.pathname !== "/content-bank") return <main><h1>Страница не найдена</h1></main>;
  if (loading) return <main><p>Загрузка справочников…</p></main>;
  return <main><h1>Новое задание</h1><p>Создайте первую draft-версию задания.</p>{error && <div className="alert error">{error}</div>}<form onSubmit={submit}>
    <label>Предмет<select required value={form.subject_id} onChange={e => change("subject_id", e.target.value)}><option value="">Выберите</option>{catalogs?.subjects.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
    <label>Класс<select required value={form.grade_id} onChange={e => change("grade_id", e.target.value)}><option value="">Выберите</option>{catalogs?.grades.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
    <label>Тема<select required value={form.topic_id} onChange={e => change("topic_id", e.target.value)}><option value="">Выберите</option>{topics.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
    <label>Подтема (необязательно)<select value={form.subtopic_id} onChange={e => change("subtopic_id", e.target.value)}><option value="">Без подтемы</option>{subtopics.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
    <label>Основной навык<select required value={form.skill_id} onChange={e => change("skill_id", e.target.value)}><option value="">Выберите</option>{skills.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
    <label>Название (необязательно)<input value={form.title} onChange={e => change("title", e.target.value)} /></label><label className="wide">Условие<textarea required value={form.statement} onChange={e => change("statement", e.target.value)} /></label>
    <label>Тип<select value={form.task_type} onChange={e => change("task_type", e.target.value)}>{Object.keys(formats).map(x => <option key={x}>{x}</option>)}</select></label><label>Формат ответа<select value={form.answer_format} onChange={e => change("answer_format", e.target.value)}>{formats[form.task_type].map(x => <option key={x}>{x}</option>)}</select></label><label>Сложность<select value={form.difficulty} onChange={e => change("difficulty", e.target.value)}>{["basic", "standard", "advanced"].map(x => <option key={x}>{x}</option>)}</select></label><label>Источник (необязательно)<input value={form.source} onChange={e => change("source", e.target.value)} /></label><button disabled={saving}>{saving ? "Создание…" : "Создать задание"}</button>
  </form>{success && <div className="alert success"><strong>Задание создано</strong><div>task_id: {success.id}</div><div>task_version_id: {success.initial_version.id}</div><div>version_no: {success.initial_version.version_no}</div><div>status: {success.initial_version.status}</div></div>}</main>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
