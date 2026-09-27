import { Show } from "solid-js";
import { ArrowUpCircle, CheckCircle2, Loader2, Sparkles } from "lucide-solid";
import { openUpdateDialog, versionInfo, versionLoading } from "@/lib/versionStore";

export function VersionBadge(props: { collapsed?: boolean }) {
  const info = () => versionInfo();
  const loading = () => versionLoading();

  return (
    <Show
      when={props.collapsed}
      fallback={
        <div class="sidebar-version-container">
          <Show
            when={info()?.has_update}
            fallback={
              <button
                type="button"
                class="sidebar-version-pill up-to-date"
                onClick={openUpdateDialog}
                title="View version information"
              >
                <span class="version-pill-icon">
                  <Show when={loading()} fallback={<CheckCircle2 size={12} class="text-success" />}>
                    <Loader2 size={12} class="spinner-animate" />
                  </Show>
                </span>
                <span class="version-pill-text">v{info()?.current_version || "..."}</span>
                <span class="version-pill-status">Up to date</span>
              </button>
            }
          >
            <button
              type="button"
              class="sidebar-version-pill update-available"
              onClick={openUpdateDialog}
              title={`New version available: v${info()?.latest_version}`}
            >
              <span class="version-pill-icon text-accent">
                <Sparkles size={13} />
              </span>
              <span class="version-pill-text">Update: v{info()?.latest_version}</span>
              <span class="version-pill-badge">New</span>
            </button>
          </Show>
        </div>
      }
    >
      <div class="sidebar-version-collapsed-wrapper">
        <button
          type="button"
          class={`sidebar-version-collapsed-btn ${info()?.has_update ? "has-update" : ""}`}
          onClick={openUpdateDialog}
          title={
            info()?.has_update
              ? `Update available: v${info()?.latest_version}`
              : `Kai Agent v${info()?.current_version || ""}`
          }
          aria-label="Version and updates"
        >
          <Show
            when={info()?.has_update}
            fallback={<span class="version-collapsed-text">v{info()?.current_version?.split(".")[0] || ""}</span>}
          >
            <ArrowUpCircle size={15} class="text-accent" />
            <span class="version-collapsed-dot" />
          </Show>
        </button>
      </div>
    </Show>
  );
}
