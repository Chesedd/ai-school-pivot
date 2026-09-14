import {useEffect, useState, type FormEvent} from "react";
import {createUser, listUsers, patchUser, replaceRoles, resetPassword, type AdminUser, type CreateUserRequest, type Role} from "./adminApi";
import {useAuth} from "./auth";
import {errorMessage} from "./errors";

const roles: Role[] = ["admin", "teacher", "student"];
const roleLabels: Record<Role, string> = {admin: "Администратор", teacher: "Учитель", student: "Ученик"};
const RoleChecks = ({value, onChange}: {value: Role[]; onChange: (value: Role[]) => void}) => <fieldset><legend>Роли</legend>{roles.map(role => <label className="check" key={role}><input type="checkbox" checked={value.includes(role)} onChange={event => onChange(event.target.checked ? [...value, role] : value.filter(value => value !== role))}/>{roleLabels[role]}</label>)}</fieldset>;
const names = (user: AdminUser) => ({first: user.first_name || (!user.last_name ? user.display_name : "—"), last: user.last_name || "—"});

type Credentials = {displayName: string; login: string; password: string};

export function AdminUsersPage() {
  const auth = useAuth();
  const [items, setItems] = useState<AdminUser[]>([]);
  const [selected, setSelected] = useState<AdminUser>();
  const [credentials, setCredentials] = useState<Credentials>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const load = async (selectId?: string) => {
    setLoading(true);
    try {
      const page = await listUsers();
      setItems(page.items);
      const id = selectId || selected?.user_id;
      if (id) setSelected(page.items.find(user => user.user_id === id));
    } catch (reason) { setError(errorMessage(reason, "Не удалось загрузить пользователей.")); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);
  const run = async (action: () => Promise<unknown>, success: string, self: "refresh" | "logout" | null = null) => {
    setBusy(true); setError(""); setMessage("");
    try {
      await action(); setMessage(success);
      if (self === "logout") auth.becomeAnonymous();
      else { await load(selected?.user_id); if (self === "refresh") await auth.refreshPrincipal(); }
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };
  const handleCreate = async (value: CreateUserRequest) => {
    setBusy(true); setError(""); setMessage(""); setCredentials(undefined);
    try {
      const created = await createUser(value);
      if (created.generated_password !== undefined) setCredentials({displayName: created.display_name, login: created.login, password: created.generated_password});
      setMessage("Пользователь создан.");
      await load(created.user_id);
    } catch (reason) { setError(errorMessage(reason, "Не удалось создать пользователя.")); }
    finally { setBusy(false); }
  };
  if (loading && !items.length) return <p role="status" className="loading-state">Загрузка пользователей…</p>;
  return <section>
    <div className="page-heading"><div><h1>Пользователи</h1><p>Учётные записи и права доступа</p></div></div>
    {error && <div role="alert" className="alert error">{error}</div>}
    {message && <p role="status" className="alert success">{message}</p>}
    {credentials && <CredentialsPanel value={credentials} onDismiss={() => setCredentials(undefined)}/>}
    <CreateUserForm busy={busy} onSubmit={handleCreate}/>
    <div className="admin-users"><div><h2>Список пользователей</h2><div className="table-wrap"><table><thead><tr><th>Имя</th><th>Фамилия</th><th>Логин</th><th>Роли</th><th>Статус</th></tr></thead><tbody>{items.map(user => { const identity = names(user); return <tr key={user.user_id}><td><button className="link-button" onClick={() => setSelected(user)}>{identity.first}</button></td><td>{identity.last}</td><td>{user.login}</td><td>{user.roles.map(role => roleLabels[role]).join(", ") || "Не назначены"}</td><td><span className="badge">{user.is_active ? "Активен" : "Отключён"}</span></td></tr>; })}</tbody></table></div></div>
      {selected && <UserEditor key={selected.updated_at + selected.roles.join()} user={selected} busy={busy} run={run} isSelf={selected.user_id === auth.principal?.user_id}/>}
    </div>
  </section>;
}

function CreateUserForm({busy, onSubmit}: {busy: boolean; onSubmit: (value: CreateUserRequest) => Promise<void>}) {
  const [firstName, setFirstName] = useState(""); const [lastName, setLastName] = useState("");
  const [mode, setMode] = useState<"generated" | "provided">("generated"); const [password, setPassword] = useState(""); const [selectedRoles, setRoles] = useState<Role[]>([]);
  const submit = async (event: FormEvent) => { event.preventDefault(); await onSubmit({first_name: firstName.trim(), last_name: lastName.trim(), roles: selectedRoles, password: mode === "generated" ? {mode} : {mode, value: password}}); setPassword(""); };
  return <details className="admin-create"><summary>Создать пользователя</summary><form onSubmit={submit}>
    <label>Имя<input required maxLength={100} autoComplete="given-name" value={firstName} onChange={event => setFirstName(event.target.value)}/></label>
    <label>Фамилия<input required maxLength={100} autoComplete="family-name" value={lastName} onChange={event => setLastName(event.target.value)}/></label>
    <RoleChecks value={selectedRoles} onChange={setRoles}/>
    <fieldset><legend>Пароль</legend><label className="check"><input type="radio" name="password-mode" checked={mode === "generated"} onChange={() => setMode("generated")}/>Сгенерировать пароль (рекомендуется)</label><label className="check"><input type="radio" name="password-mode" checked={mode === "provided"} onChange={() => setMode("provided")}/>Ввести пароль вручную</label></fieldset>
    {mode === "provided" && <label>Пароль<input required type="password" autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)}/></label>}
    <button disabled={busy}>Создать</button>
  </form></details>;
}

function CredentialsPanel({value, onDismiss}: {value: Credentials; onDismiss: () => void}) {
  const [copyError, setCopyError] = useState("");
  const copy = async (text: string) => { setCopyError(""); try { await navigator.clipboard.writeText(text); } catch { setCopyError("Не удалось скопировать. Выделите данные и скопируйте их вручную."); } };
  return <section className="credentials-panel" role="status" aria-live="polite" aria-label="Данные нового пользователя">
    <h2>Данные для входа</h2><p><strong>Сохраните их сейчас: сгенерированный пароль показывается только один раз.</strong></p>
    <dl><div><dt>Имя:</dt><dd>{value.displayName}</dd></div><div><dt>Логин:</dt><dd>{value.login}</dd></div><div><dt>Пароль:</dt><dd>{value.password}</dd></div></dl>
    {copyError && <p role="alert" className="field-error">{copyError}</p>}
    <div className="button-row"><button type="button" className="secondary" aria-label="Скопировать логин" onClick={() => void copy(value.login)}>Скопировать логин</button><button type="button" className="secondary" aria-label="Скопировать пароль" onClick={() => void copy(value.password)}>Скопировать пароль</button><button type="button" onClick={() => void copy(`Логин: ${value.login}\nПароль: ${value.password}`)}>Скопировать логин и пароль</button><button type="button" className="secondary" onClick={onDismiss}>Закрыть</button></div>
  </section>;
}

function UserEditor({user, busy, run, isSelf}: {user: AdminUser; busy: boolean; isSelf: boolean; run: (action: () => Promise<unknown>, success: string, self: "refresh" | "logout" | null) => Promise<void>}) {
  const [firstName, setFirstName] = useState(user.first_name ?? user.display_name); const [lastName, setLastName] = useState(user.last_name ?? ""); const [selectedRoles, setRoles] = useState<Role[]>(user.roles);
  const [password, setPassword] = useState(""); const [confirmation, setConfirmation] = useState(""); const [localError, setLocalError] = useState("");
  return <article className="user-editor"><h2>{user.display_name}</h2><p>Логин: <strong>{user.login}</strong></p>
    <form onSubmit={event => { event.preventDefault(); void run(() => patchUser(user.user_id, {first_name: firstName.trim(), last_name: lastName.trim()}), "Данные пользователя сохранены.", isSelf ? "refresh" : null); }}><label>Имя<input required maxLength={100} value={firstName} onChange={event => setFirstName(event.target.value)}/></label><label>Фамилия<input required maxLength={100} value={lastName} onChange={event => setLastName(event.target.value)}/></label><button disabled={busy}>Сохранить данные</button></form>
    <section><RoleChecks value={selectedRoles} onChange={setRoles}/><button disabled={busy} onClick={() => { if (confirm("Заменить набор ролей пользователя?")) void run(() => replaceRoles(user.user_id, selectedRoles), "Роли сохранены.", isSelf ? "refresh" : null); }}>Сохранить роли</button></section>
    <section><h3>Статус</h3><button className={user.is_active ? "danger" : ""} disabled={busy} onClick={() => { const next = !user.is_active; if (next || confirm("Пользователь будет отключён и выйдет из активных сессий.")) void run(() => patchUser(user.user_id, {is_active: next}), next ? "Пользователь активирован." : "Пользователь отключён.", isSelf && !next ? "logout" : isSelf ? "refresh" : null); }}>{user.is_active ? "Отключить" : "Активировать"}</button></section>
    <form onSubmit={event => { event.preventDefault(); setLocalError(""); if (password !== confirmation) { setLocalError("Пароли не совпадают."); return; } if (!confirm("Сбросить пароль и завершить активные сессии пользователя?")) return; void run(() => resetPassword(user.user_id, password), "Пароль изменён. Пользователю нужно войти снова.", isSelf ? "logout" : null).then(() => { setPassword(""); setConfirmation(""); }); }}><h3>Сброс пароля</h3><label>Новый пароль<input required type="password" autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)}/></label><label>Подтверждение пароля<input required type="password" autoComplete="new-password" value={confirmation} onChange={event => setConfirmation(event.target.value)}/></label>{localError && <p role="alert" className="field-error">{localError}</p>}<button disabled={busy}>Сбросить пароль</button></form>
  </article>;
}
