import { useLocation, useNavigate } from "@solidjs/router";
import { BrainCircuit, Globe, ServerCog, Workflow } from "lucide-solid";
import { SettingsLayout } from "./SettingsLayout";
import { SettingsTabBar, type SettingsTabItem } from "./shared";

import { currentProviderTab, type ProviderTab } from "@/lib/providerRoutes";
export { currentProviderTab } from "@/lib/providerRoutes";

const TABS: ReadonlyArray<SettingsTabItem<ProviderTab>> = [
  { value: "llm", label: "Models", icon: () => <Workflow size={13} /> },
  { value: "web", label: "Search & Web", icon: () => <Globe size={13} /> },
  { value: "workspace", label: "Execution Environments", icon: () => <ServerCog size={13} /> },
  { value: "decision", label: "Decision Model", icon: () => <BrainCircuit size={13} /> },
];

export function ProviderSettingsLayout(props: Parameters<typeof SettingsLayout>[0]) {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <SettingsLayout {...props} breadcrumbLabel="Providers" contentWidth="wide">
      <SettingsTabBar
        items={TABS}
        value={currentProviderTab(location.pathname, location.search)}
        ariaLabel="Provider category"
        onChange={(tab) => navigate(`/settings/providers?tab=${tab}`)}
      />
      {props.children}
    </SettingsLayout>
  );
}
