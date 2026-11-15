import { useLocation, useParams } from "@solidjs/router";
import { Match, Show, Switch } from "solid-js";
import { BackendsPage } from "./BackendsPage";
import { DecisionProvidersPage } from "./DecisionProvidersPage";
import { ProvidersPage } from "./ProvidersPage";
import { ModelPoolsPage } from "./ModelPoolsPage";
import { currentConnectionKind, currentProviderSection } from "@/lib/providerRoutes";
import { ProviderDeskPage } from "./ProviderDeskPage";
import { WebConnectionsPage } from "./WebConnectionsPage";

export function ProviderHubPage() {
  const location = useLocation();
  const params = useParams();
  const tab = () => currentProviderSection(location.pathname, location.search);
  const creating = () => new URLSearchParams(location.search).has("new");
  const kind = () => currentConnectionKind(location.pathname, location.search) || "llm";
  return (
    <Switch fallback={<>
      <ProviderDeskPage />
      <Show when={creating()}><Switch>
        <Match when={kind() === "llm"}><ProvidersPage /></Match>
        <Match when={kind() === "decision"}><DecisionProvidersPage /></Match>
        <Match when={kind() === "web"}><WebConnectionsPage /></Match>
      </Switch></Show>
    </>}>
      <Match when={params.providerName}><ProvidersPage /></Match>
      <Match when={params.connectionName}><WebConnectionsPage /></Match>
      <Match when={params.decisionProviderName}><DecisionProvidersPage /></Match>
      <Match when={tab() === "pools"}><ModelPoolsPage /></Match>
      <Match when={tab() === "workspace"}><BackendsPage /></Match>
    </Switch>
  );
}
