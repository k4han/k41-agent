import { createSignal, Show } from "solid-js";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Download,
  ExternalLink,
  GitBranch,
  Info,
  Loader2,
  RefreshCw,
  Sparkles,
  Terminal,
} from "lucide-solid";

import { CopyButton } from "@/components/CopyButton";
import { Dialog } from "@/components/Dialog";
import { Markdown } from "@/components/Markdown";
import {
  checkForUpdates,
  closeUpdateDialog,
  isUpdateDialogOpen,
  startSystemUpdate,
  updateError,
  updateState,
  versionInfo,
  versionLoading,
  versionCheckError,
} from "@/lib/versionStore";

export function UpdateDialog() {
  const info = () => versionInfo();
  const state = () => updateState();
  const loading = () => versionLoading();
  const error = () => updateError();
  const [updating, setUpdating] = createSignal(false);

  const handleStartUpdate = async () => {
    setUpdating(true);
    try {
      await startSystemUpdate();
    } finally {
      setUpdating(false);
    }
  };

  const handleManualCheck = async () => {
    await checkForUpdates(true);
  };

  return (
    <Dialog
      open={isUpdateDialogOpen()}
      onClose={closeUpdateDialog}
      title={
        <div class="update-dialog-header-title">
          <Sparkles size={18} class="text-accent" />
          <span>Kai Agent Updates</span>
        </div>
      }
      subtitle="Manage version and system updates"
      size="lg"
      showCloseButton={state() === "idle" || state() === "error"}
      closeOnBackdrop={state() === "idle" || state() === "error"}
      closeOnEscape={state() === "idle" || state() === "error"}
      footer={
        <Show when={state() === "idle" || state() === "error"}>
          <div class="update-dialog-footer">
            <div class="update-dialog-footer-left">
              <button
                type="button"
                class="btn btn-secondary btn-sm"
                onClick={handleManualCheck}
                disabled={loading()}
              >
                <RefreshCw size={13} class={loading() ? "spinner-animate" : ""} />
                <span>Check for updates</span>
              </button>
            </div>
            <div class="update-dialog-footer-right">
              <button
                type="button"
                class="btn btn-ghost btn-sm"
                onClick={closeUpdateDialog}
              >
                Close
              </button>
              <Show when={info()?.has_update && info()?.install_type === "managed"}>
                <button
                  type="button"
                  class="btn btn-primary btn-sm"
                  onClick={handleStartUpdate}
                  disabled={updating() || loading() || Boolean(versionCheckError())}
                >
                  <Download size={13} />
                  <span>Update to v{info()?.latest_version}</span>
                </button>
              </Show>
            </div>
          </div>
        </Show>
      }
    >
      <div class="update-dialog-body">
        {/* Active Updating View */}
        <Show when={state() === "updating" || state() === "reconnecting"}>
          <div class="update-dialog-progress-state">
            <div class="update-dialog-spinner-container">
              <Loader2 size={40} class="spinner-animate text-accent" />
            </div>
            <h3 class="update-dialog-progress-title">
              {state() === "updating" ? "Downloading and Applying Update..." : "Restarting Server & Reconnecting..."}
            </h3>
            <p class="update-dialog-progress-desc">
              {state() === "updating"
                ? "The release artifact is being downloaded, unpacked, and dependencies synced."
                : "The agent server is restarting with the new version. Your dashboard will automatically reload once ready."}
            </p>
            <div class="update-dialog-progress-warning">
              <Info size={14} />
              <span>Please keep this window open. Downloading and installing dependencies may take several minutes.</span>
            </div>
          </div>
        </Show>

        {/* Success View */}
        <Show when={state() === "success"}>
          <div class="update-dialog-progress-state">
            <div class="update-dialog-spinner-container text-success">
              <CheckCircle2 size={42} />
            </div>
            <h3 class="update-dialog-progress-title">Update Completed!</h3>
            <p class="update-dialog-progress-desc">
              Kai Agent is running v{info()?.current_version}. Reloading now...
            </p>
          </div>
        </Show>

        {/* Error View */}
        <Show when={state() === "error"}>
          <div class="update-dialog-progress-state">
            <div class="update-dialog-spinner-container text-danger">
              <AlertCircle size={42} />
            </div>
            <h3 class="update-dialog-progress-title">Update Failed</h3>
            <p class="update-dialog-progress-desc text-danger">{error()}</p>
            <p class="update-dialog-progress-subdesc">
              Check server.log or ~/.k41-agent/update.log for the installation and recovery status.
            </p>
          </div>
        </Show>

        {/* Normal / Idle View */}
        <Show when={state() === "idle"}>
          <Show when={versionCheckError()}>
            <div class="update-dev-notice" role="alert">
              <div class="update-dev-notice-header">
                <AlertCircle size={16} class="text-danger" />
                <strong>Could not check for updates</strong>
              </div>
              <p class="update-dev-notice-text">{versionCheckError()}</p>
            </div>
          </Show>
          {/* Version comparison card */}
          <div class="update-version-card">
            <div class="update-version-col">
              <div class="update-version-label">Current Version</div>
              <div class="update-version-value">v{info()?.current_version || "..."}</div>
            </div>
            <div class="update-version-arrow">
              <ArrowRight size={18} />
            </div>
            <div class="update-version-col">
              <div class="update-version-label">Latest Release</div>
              <div class="update-version-value">
                v{info()?.latest_version || "..."}
                <Show when={info()?.has_update}>
                  <span class="update-badge-pill update-badge-new">New</span>
                </Show>
                <Show when={info() && !info()?.has_update && !versionCheckError()}>
                  <span class="update-badge-pill update-badge-current">Latest</span>
                </Show>
              </div>
            </div>
          </div>

          {/* Development Mode Notice */}
          <Show when={info()?.install_type === "development"}>
            <div class="update-dev-notice">
              <div class="update-dev-notice-header">
                <GitBranch size={16} class="text-accent" />
                <strong>Development Mode Detected</strong>
              </div>
              <p class="update-dev-notice-text">
                This instance is running from source code. Automatic zip updates are reserved for managed installs.
                To update your local repository, run:
              </p>
              <div class="update-dev-code-row">
                <code>git pull && uv sync</code>
                <CopyButton value="git pull && uv sync" />
              </div>
            </div>
          </Show>

          {/* Release Notes */}
          <Show when={info()?.has_update}>
            <div class="update-release-section">
              <div class="update-release-header">
                <div>
                  <h4 class="update-release-title">{info()?.release_name || `Release v${info()?.latest_version}`}</h4>
                  <Show when={info()?.published_at}>
                    <div class="update-release-date">
                      Published {new Date(info()!.published_at!).toLocaleDateString()}
                    </div>
                  </Show>
                </div>
                <Show when={info()?.release_url}>
                  <a
                    href={info()!.release_url}
                    target="_blank"
                    rel="noreferrer"
                    class="update-release-link"
                    title="View release on GitHub"
                  >
                    <span>GitHub Release</span>
                    <ExternalLink size={12} />
                  </a>
                </Show>
              </div>

              <div class="update-release-notes-box">
                <Show
                  when={info()?.release_notes}
                  fallback={<p class="update-no-notes">No release notes provided for this version.</p>}
                >
                  <Markdown text={info()!.release_notes} />
                </Show>
              </div>
            </div>
          </Show>

          <Show when={info() && !info()?.has_update && !versionCheckError()}>
            <div class="update-up-to-date-box">
              <CheckCircle2 size={24} class="text-success" />
              <div>
                <div class="update-up-to-date-title">Kai Agent is up to date</div>
                <div class="update-up-to-date-desc">
                  You are currently running the latest version (v{info()?.current_version}).
                </div>
              </div>
            </div>
          </Show>
        </Show>
      </div>
    </Dialog>
  );
}
