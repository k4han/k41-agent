import { Show } from "solid-js";
import { useParams, useSearchParams } from "@solidjs/router";
import { CalendarDays, FolderGit2, PlugZap } from "lucide-solid";

import { GoogleCalendarTab } from "./connections/GoogleCalendarTab";
import { McpTab } from "./connections/McpTab";
import { GitHubSettingsPage, RepositoriesTab } from "./connections/RepositoriesTab";
import { SettingsLayout } from "./SettingsLayout";
import { SettingsTabBar, type SettingsTabItem } from "./shared";

type TabKey = "repositories" | "mcp" | "google-calendar";

const TAB_ITEMS: ReadonlyArray<SettingsTabItem<TabKey>> = [
  { value: "repositories", label: "Repositories", icon: () => <FolderGit2 size={13} /> },
  { value: "mcp", label: "MCP Servers", icon: () => <PlugZap size={13} /> },
  { value: "google-calendar", label: "Google Calendar", icon: () => <CalendarDays size={13} /> },
];

export function ConnectionsPage() {
  const params = useParams<{ subpage?: string }>();
  const [searchParams, setSearchParams] = useSearchParams<{ tab?: string }>();
  const tab = (): TabKey => {
    const t = searchParams.tab;
    if (t === "mcp") {
      return "mcp";
    }
    if (t === "google-calendar" || t === "calendar" || t === "google") {
      return "google-calendar";
    }
    if (t === "github") {
      return "repositories";
    }
    return "repositories";
  };

  return (
    <Show
      when={params.subpage === "github"}
      fallback={
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
          <Show when={tab() === "google-calendar"}>
            <GoogleCalendarTab />
          </Show>
        </SettingsLayout>
      }
    >
      <GitHubSettingsPage />
    </Show>
  );
}
