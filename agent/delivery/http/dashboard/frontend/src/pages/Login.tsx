import { createSignal } from "solid-js";
import { Bot, LogIn } from "lucide-solid";

import { readError } from "@/lib/api";

const DEFAULT_ADMIN_PASSWORD = "1234";

export function LoginPage() {
  const [password, setPassword] = createSignal(DEFAULT_ADMIN_PASSWORD);
  const [error, setError] = createSignal("");
  const [loading, setLoading] = createSignal(false);

  const submit = async (event: Event) => {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const response = await fetch("/login", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json",
        },
        credentials: "same-origin",
        body: JSON.stringify({ password: password() }),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main class="auth-page">
      <section class="panel auth-panel">
        <div class="panel-body stack">
          <div class="row">
            <div class="brand-mark">
              <Bot size={16} />
            </div>
            <div>
              <h1 class="page-title">Kai Console</h1>
              <p class="page-subtitle">Sign in with the admin password.</p>
              <p class="hint">
                Default password: <span class="mono">{DEFAULT_ADMIN_PASSWORD}</span>
              </p>
            </div>
          </div>
          <form class="stack" onSubmit={submit}>
            <div class="field">
              <label>Password</label>
              <input
                class="input"
                type="password"
                value={password()}
                onInput={(event) => setPassword(event.currentTarget.value)}
                autofocus
              />
            </div>
            {error() ? <div class="badge badge-danger">{error()}</div> : null}
            <button class="btn btn-primary" type="submit" disabled={loading()}>
              <LogIn size={14} />
              {loading() ? "Signing in..." : "Sign In"}
            </button>
          </form>
        </div>
      </section>
    </main>
  );
}

