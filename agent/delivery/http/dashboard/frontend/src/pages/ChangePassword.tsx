import { createSignal } from "solid-js";
import { KeyRound } from "lucide-solid";

import { postJson } from "@/lib/api";

export function ChangePasswordPage() {
  const [currentPassword, setCurrentPassword] = createSignal("1234");
  const [newPassword, setNewPassword] = createSignal("");
  const [confirmation, setConfirmation] = createSignal("");
  const [error, setError] = createSignal("");
  const [loading, setLoading] = createSignal(false);

  const submit = async (event: Event) => {
    event.preventDefault();
    setError("");
    if (newPassword() !== confirmation()) {
      setError("Passwords do not match.");
      return;
    }
    setLoading(true);
    try {
      await postJson("/change-password", {
        old_password: currentPassword(),
        new_password: newPassword(),
      });
      window.location.href = "/login?status=password-changed";
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to change password");
    } finally {
      setLoading(false);
    }
  };

  const logout = async () => {
    setLoading(true);
    try {
      await postJson("/logout", {});
      window.location.href = "/login";
    } catch (err) {
      setError(err instanceof Error ? err.message : "Logout failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main class="auth-page">
      <section class="panel auth-panel">
        <div class="panel-body stack">
          <h1 class="page-title">Set a new password</h1>
          <p class="page-subtitle">Replace the default password before using Kai Console.</p>
          <p class="hint">Use at least 8 characters, including uppercase, lowercase, a number, and a special character. Sign in again after saving.</p>
          <form class="stack" onSubmit={submit}>
            <div class="field">
              <label for="current-password">Current password</label>
              <input id="current-password" class="input" type="password" autocomplete="current-password" required
                value={currentPassword()} onInput={(event) => setCurrentPassword(event.currentTarget.value)} />
            </div>
            <div class="field">
              <label for="new-password">New password</label>
              <input id="new-password" class="input" type="password" autocomplete="new-password" required minLength={8} autofocus
                value={newPassword()} onInput={(event) => setNewPassword(event.currentTarget.value)} />
            </div>
            <div class="field">
              <label for="confirm-password">Confirm new password</label>
              <input id="confirm-password" class="input" type="password" autocomplete="new-password" required minLength={8}
                value={confirmation()} onInput={(event) => setConfirmation(event.currentTarget.value)} />
            </div>
            {error() ? <div class="badge badge-danger" role="alert">{error()}</div> : null}
            <button class="btn btn-primary" type="submit" disabled={loading()}>
              <KeyRound size={14} />
              {loading() ? "Saving..." : "Save new password"}
            </button>
            <button class="btn" type="button" disabled={loading()} onClick={logout}>Log out</button>
          </form>
        </div>
      </section>
    </main>
  );
}
