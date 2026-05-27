import { Show } from "solid-js";
import { useSearchParams } from "@solidjs/router";

import { GitHubTab } from "./connections/GitHubTab";
import { McpTab } from "./connections/McpTab";
import { SettingsLayout } from "./SettingsLayout";

type TabKey = "github" | "mcp";

export function ConnectionsPage() {
  const [searchParams, setSearchParams] = useSearchParams<{ tab?: string }>();
  const tab = () => {
    const t = searchParams.tab;
    return t === "mcp" ? "mcp" : "github";
  };

  return (
    <SettingsLayout
      title="Connections"
      subtitle="Manage external service connections."
      breadcrumbLabel="Connections"
      contentWidth="wide"
    >
      <div class="tab-bar">
        <button
          class={`btn btn-sm ${tab() === "github" ? "btn-primary" : ""}`}
          type="button"
          onClick={() => setSearchParams({ tab: "github" })}
        >
          GitHub
        </button>
        <button
          class={`btn btn-sm ${tab() === "mcp" ? "btn-primary" : ""}`}
          type="button"
          onClick={() => setSearchParams({ tab: "mcp" })}
        >
          MCP Servers
        </button>
      </div>

      <Show when={tab() === "github"}>
        <GitHubTab />
      </Show>
      <Show when={tab() === "mcp"}>
        <McpTab />
      </Show>
    </SettingsLayout>
  );
}
