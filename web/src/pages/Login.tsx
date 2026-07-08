import { useState } from "react";
import { useAuth } from "../context/AuthContext";

// Plain HTML form (NOT HTTP Basic) so the OS password manager offers autofill /
// FaceID via autocomplete hints.
export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await login(username.trim(), password);
    } catch {
      setErr("Неверный логин или пароль");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="screen center">
      <form className="card login" onSubmit={submit}>
        <h1 className="brand">Carousel Studio</h1>
        <p className="muted">Вход в кабинет</p>
        <input
          name="username"
          autoComplete="username"
          placeholder="Логин"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoCapitalize="none"
          autoCorrect="off"
        />
        <input
          name="password"
          type="password"
          autoComplete="current-password"
          placeholder="Пароль"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {err && <div className="error">{err}</div>}
        <button className="primary" disabled={busy || !username || !password}>
          {busy ? "Входим…" : "Войти"}
        </button>
      </form>
    </div>
  );
}
