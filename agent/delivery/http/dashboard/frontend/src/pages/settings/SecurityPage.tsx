import { createSignal } from "solid-js";
import { KeyRound, Save } from "lucide-solid";

import { useToast } from "@/components/Toast";
import { postJson } from "@/lib/api";

import { SettingsLayout } from "./SettingsLayout";

export function SecurityPage() {
  const [oldPassword, setOldPassword] = createSignal("");
  const [newPassword, setNewPassword] = createSignal("");
  const [loading, setLoading] = createSignal(false);
  const { showToast } = useToast();

  const submit = async (event: Event) => {
    event.preventDefault();
    setLoading(true);
    try {
      await postJson("/change-password", {
        old_password: oldPassword(),
        new_password: newPassword(),
      });
      setOldPassword("");
      setNewPassword("");
      showToast("Password changed.");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to change password", "error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <SettingsLayout
      title="Security"
      subtitle="Update the dashboard admin password."
      contentWidth="narrow"
    >
      <section class="panel">
        <div class="panel-header">
          <div class="panel-title row">
            <KeyRound size={14} />
            Credentials
          </div>
        </div>
        <div class="panel-body">
          <form class="stack" onSubmit={submit}>
            <div class="field">
              <label>Current Password</label>
              <input
                class="input"
                type="password"
                value={oldPassword()}
                onInput={(event) => setOldPassword(event.currentTarget.value)}
              />
            </div>
            <div class="field">
              <label>New Password</label>
              <input
                class="input"
                type="password"
                value={newPassword()}
                onInput={(event) => setNewPassword(event.currentTarget.value)}
              />
            </div>
            <button class="btn btn-primary" type="submit" disabled={loading()}>
              <Save size={14} />
              {loading() ? "Saving..." : "Save Password"}
            </button>
          </form>
        </div>
      </section>
    </SettingsLayout>
  );
}
