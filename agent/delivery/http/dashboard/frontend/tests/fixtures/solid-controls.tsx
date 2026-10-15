import { createSignal } from "solid-js";
import { render } from "solid-js/web";

import { FormMultiSelect } from "@/components/FormSelect";
import { ToastProvider } from "@/components/Toast";
import { McpInstallDialog } from "@/pages/settings/connections/McpInstallDialog";
import type { McpInstallTarget, McpSearchResult } from "@/types";

function installTarget(id: string, command: string, key: string): McpInstallTarget {
  return {
    id, label: id, transport: "stdio", registry_type: "npm", runtime_hint: "node",
    command, args: [], url: "", env_template: {}, headers_template: {},
    required_inputs: [{ key, label: key, description: "", required: true, secret: true, default: "", placeholder: key, source: "env" }],
  };
}

const server: McpSearchResult = {
  registry_name: "example/first", title: "First server", description: "First description", version: "1",
  is_latest: true, verified: true, repository_url: "", website_url: "", required_inputs: [], auth_summary: "Token",
  install_targets: [installTarget("first", "first-command", "TOKEN_A"), installTarget("second", "second-command", "TOKEN_B")],
};
const options = [{ value: "a", label: "Alpha" }, { value: "b", label: "Beta" }, { value: "c", label: "Disabled", disabled: true }];

function Controls() {
  const [values, setValues] = createSignal<string[]>([]);
  const [limit, setLimit] = createSignal(1);
  const [open, setOpen] = createSignal(false);
  const [selectedServer, setSelectedServer] = createSignal(server);
  const submissions: unknown[] = [];
  Object.assign(window, {
    solidControls: {
      values, setValues, setLimit, submissions,
      openMcp: () => setOpen(true),
      replaceServer: () => setSelectedServer({
        ...server, registry_name: "example/replacement", title: "Replacement server", description: "Replacement description",
        install_targets: [installTarget("replacement", "replacement-command", "TOKEN_C")],
      }),
    },
  });
  return (
    <ToastProvider>
      <FormMultiSelect values={values()} onChange={setValues} options={options} maxSelections={limit()} ariaLabel="Test choices" />
      <McpInstallDialog
        open={open()} server={selectedServer()} agentNames={["default"]} onClose={() => setOpen(false)}
        onSubmit={(payload) => { submissions.push(payload); }}
      />
    </ToastProvider>
  );
}

render(() => <Controls />, document.getElementById("root")!);
