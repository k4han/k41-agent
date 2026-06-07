import { Show } from "solid-js";
import { useSearchParams } from "@solidjs/router";
import { FolderGit2, PlugZap } from "lucide-solid";

import { McpTab } from "./connections/McpTab";
import { RepositoriesTab } from "./connections/RepositoriesTab";
import { SettingsLayout } from "./SettingsLayout";
import { SettingsTabBar, type SettingsTabItem } from "./shared";

type TabKey = "repositories" | "mcp";

const TAB_ITEMS: ReadonlyArray<SettingsTabItem<TabKey>> = [
  { value: "repositories", label: "Repositories", icon: () => <FolderGit2 size={13} /> },
  { value: "mcp", label: "MCP Servers", icon: () => <PlugZap size={13} /> },
];

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
      breadcrumbLabel="Connections"
      contentWidth="wide"
    >
      <SettingsTabBar
        items={TAB_ITEMS}
        value={tab()}
        ariaLabel="Connection category"
        onChange={(value) => setSearchParams({ tab: value })}
      />

      <Show when={tab() === "repositories"}>
        <RepositoriesTab />
      </Show>
      <Show when={tab() === "mcp"}>
        <McpTab />
      </Show>
    </SettingsLayout>
  );
}
