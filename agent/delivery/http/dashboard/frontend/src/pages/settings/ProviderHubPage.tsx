import { useLocation } from "@solidjs/router";
import { Match, Switch } from "solid-js";
import { BackendsPage } from "./BackendsPage";
import { DecisionProvidersPage } from "./DecisionProvidersPage";
import { ProvidersPage } from "./ProvidersPage";
import { currentProviderTab } from "./ProviderSettingsLayout";
import { WebConnectionsPage } from "./WebConnectionsPage";

export function ProviderHubPage() {
  const location = useLocation();
  const tab = () => currentProviderTab(location.pathname, location.search);
  return (
    <Switch>
      <Match when={tab() === "llm"}><ProvidersPage /></Match>
      <Match when={tab() === "web"}><WebConnectionsPage /></Match>
      <Match when={tab() === "workspace"}><BackendsPage /></Match>
      <Match when={tab() === "decision"}><DecisionProvidersPage /></Match>
    </Switch>
  );
}
