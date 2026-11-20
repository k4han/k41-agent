import "@/styles/routes/providers.css";

import { useLocation, useNavigate } from "@solidjs/router";
import { BookOpen, Layers, Plug, ServerCog, Star } from "lucide-solid";
import { Show } from "solid-js";
import { Dialog } from "@/components/Dialog";
import { SettingsLayout } from "./SettingsLayout";
import { SettingsTabBar, type SettingsTabItem } from "./shared";

import { connectionReturnTo, currentConnectionKind, currentProviderSection, type ProviderSection } from "@/lib/providerRoutes";
export { currentProviderTab } from "@/lib/providerRoutes";

const TABS: ReadonlyArray<SettingsTabItem<ProviderSection>> = [
  { value: "connections", label: "Connections", icon: () => <Plug size={13} /> },
  { value: "catalog", label: "Provider catalog", icon: () => <BookOpen size={13} /> },
  { value: "pools", label: "Model Pools", icon: () => <Layers size={13} /> },
  { value: "defaults", label: "Defaults", icon: () => <Star size={13} /> },
  { value: "workspace", label: "Execution Environments", icon: () => <ServerCog size={13} /> },
];

export function ProviderSettingsLayout(props: Parameters<typeof SettingsLayout>[0] & { createDialog?: boolean }) {
  const location = useLocation();
  const navigate = useNavigate();
  const isCreate = () => props.createDialog !== false && location.pathname === "/settings/providers" && new URLSearchParams(location.search).has("new");
  const closeCreate = () => {
    const query = new URLSearchParams(location.search);
    const returnTo = query.get("returnTo") || "";
    navigate(returnTo && connectionReturnTo(returnTo) === returnTo ? returnTo
      : `/settings/providers?tab=catalog&kind=${currentConnectionKind(location.pathname, location.search) || "llm"}`);
  };
  return (
    <Show when={!isCreate()} fallback={<Dialog open title={props.title} size="lg" onClose={closeCreate}><div class="provider-create-dialog">{props.children}</div></Dialog>}>
    <SettingsLayout {...props} hideHeading={props.hideHeading ?? false} breadcrumbLabel="Providers" contentWidth="wide">
      <SettingsTabBar
        items={TABS}
        value={currentProviderSection(location.pathname, location.search)}
        ariaLabel="Provider category"
        onChange={(tab) => navigate(`/settings/providers?tab=${tab}`)}
      />
      {props.children}
    </SettingsLayout>
    </Show>
  );
}
