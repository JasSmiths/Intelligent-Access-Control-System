import { Check, Key, Lock, LogIn, MessageCircle, Shield, ShieldCheck, UserRound } from "lucide-react";
import React from "react";
import { api } from "../api/client";
import type { UserAccount } from "../api/types";
export type AuthStatus = {
  setup_required: boolean;
  authenticated: boolean;
  user: UserAccount | null;
};
export function AuthLoading() {
  return (
    <main className="auth-page">
      <section className="auth-card compact">
        <div className="auth-mark">
          <ShieldCheck size={28} />
        </div>
        <h1>Intelligent</h1>
        <p>Checking secure session</p>
      </section>
    </main>
  );
}
export function LoginPage({ onLogin }: { onLogin: (user: UserAccount) => void }) {
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [rememberMe, setRememberMe] = React.useState(true);
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const user = await api.post<UserAccount>("/api/v1/auth/login", {
        username,
        password,
        remember_me: rememberMe
      });
      clearChatTeaserDismissals();
      onLogin(user);
    } catch (authError) {
      setError(authError instanceof Error ? authError.message : "Invalid credentials");
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <main className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-mark">
          <ShieldCheck size={30} />
        </div>
        <div>
          <h1>Welcome back</h1>
          <p>Sign in to Crest House access control.</p>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <label className="field">
          <span>Username</span>
          <div className="field-control">
            <UserRound size={17} />
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required />
          </div>
        </label>
        <label className="field">
          <span>Password</span>
          <div className="field-control">
            <Lock size={17} />
            <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" required />
          </div>
        </label>
        <label className="check-row">
          <input checked={rememberMe} onChange={(event) => setRememberMe(event.target.checked)} type="checkbox" />
          <span>Remember me on this device</span>
        </label>
        <button className="primary-button auth-submit" disabled={submitting} type="submit">
          <LogIn size={17} />
          {submitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </main>
  );
}
export function SetupPage({ onComplete }: { onComplete: (user: UserAccount) => void }) {
  const [form, setForm] = React.useState({
    username: "",
    first_name: "Jason",
    last_name: "Smith",
    email: "",
    password: ""
  });
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const update = (field: keyof typeof form, value: string) => setForm((current) => ({ ...current, [field]: value }));
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const user = await api.post<UserAccount>("/api/v1/auth/setup", {
        username: form.username,
        first_name: form.first_name,
        last_name: form.last_name,
        email: form.email || null,
        password: form.password
      });
      clearChatTeaserDismissals();
      onComplete(user);
    } catch (setupError) {
      setError(setupError instanceof Error ? setupError.message : "Setup failed");
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <main className="auth-page">
      <form className="auth-card setup-card" onSubmit={submit}>
        <div className="auth-mark">
          <Shield size={30} />
        </div>
        <div>
          <h1>First-run setup</h1>
          <p>Create the master Admin account. This setup locks once the first user exists.</p>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <div className="field-grid">
          <label className="field">
            <span>First name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.first_name} onChange={(event) => update("first_name", event.target.value)} autoComplete="given-name" required />
            </div>
          </label>
          <label className="field">
            <span>Last name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.last_name} onChange={(event) => update("last_name", event.target.value)} autoComplete="family-name" required />
            </div>
          </label>
        </div>
        <div className="field-grid">
          <label className="field">
            <span>Username</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.username} onChange={(event) => update("username", event.target.value)} autoComplete="username" required />
            </div>
          </label>
        </div>
        <label className="field">
          <span>Email</span>
          <div className="field-control">
            <MessageCircle size={17} />
            <input value={form.email} onChange={(event) => update("email", event.target.value)} type="email" autoComplete="email" />
          </div>
        </label>
        <label className="field">
          <span>Password</span>
          <div className="field-control">
            <Key size={17} />
            <input value={form.password} onChange={(event) => update("password", event.target.value)} type="password" autoComplete="new-password" minLength={10} required />
          </div>
        </label>
        <button className="primary-button auth-submit" disabled={submitting} type="submit">
          <Check size={17} />
          {submitting ? "Creating admin..." : "Create Admin"}
        </button>
      </form>
    </main>
  );
}
function clearChatTeaserDismissals() {
  for (const key of Object.keys(sessionStorage)) {
    if (key.startsWith("iacs-chat-teaser-dismissed")) {
      sessionStorage.removeItem(key);
    }
  }
}
