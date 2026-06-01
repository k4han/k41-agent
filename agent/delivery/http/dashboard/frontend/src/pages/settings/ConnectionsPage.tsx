import { Show } from "solid-js";
import { useSearchParams } from "@solidjs/router";

import { McpTab } from "./connections/McpTab";
import { RepositoriesTab } from "./connections/RepositoriesTab";
import { SettingsLayout } from "./SettingsLayout";

type TabKey = "repositories" | "mcp";

export function ConnectionsPage() {
  const [searchParams, setSearchParams] = useSearchParams<{ tab?: string }>();
  const tab = (): TabKey => {
    const t = searchParams.tab;
    if (t === "mcp") {
      return "mcp";
    }
    if (t === "github") {
      return "repositories";
    }
    return "repositories";
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
          class={`btn btn-sm ${tab() === "repositories" ? "btn-primary" : ""}`}
          type="button"
          onClick={() => setSearchParams({ tab: "repositories" })}
        >
          Repositories
        </button>
        <button
          class={`btn btn-sm ${tab() === "mcp" ? "btn-primary" : ""}`}
          type="button"
          onClick={() => setSearchParams({ tab: "mcp" })}
        >
          MCP Servers
        </button>
      </div>

      <Show when={tab() === "repositories"}>
        <RepositoriesTab />
      </Show>
      <Show when={tab() === "mcp"}>
        <McpTab />
      </Show>
    </SettingsLayout>
  );
}
