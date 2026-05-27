import { createSignal, For, Show } from "solid-js";

import { Dialog } from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import type { McpPopularServer, McpServerInput } from "@/types";

export function McpInstallDialog(props: {
  open: boolean;
  server: McpPopularServer | null;
  onClose: () => void;
  onSubmit: (payload: McpServerInput) => Promise<void> | void;
}) {
  const { showToast } = useToast();
  const [name, setName] = createSignal("");
  const [values, setValues] = createSignal<Record<string, string>>({});
  const [submitting, setSubmitting] = createSignal(false);

  const resetForm = () => {
    setName(props.server?.id ?? "");
    const initial: Record<string, string> = {};
    for (const field of props.server?.env_fields ?? []) {
      initial[field.key] = "";
    }
    setValues(initial);
  };

  // Re-initialize when a new server is opened.
  let lastServerId: string | null = null;
  const ensureForm = () => {
    if (props.server && props.server.id !== lastServerId) {
      lastServerId = props.server.id;
      resetForm();
    }
  };

  const buildPayload = (): McpServerInput | null => {
    const server = props.server;
    if (!server) return null;
    const finalValues = values();

    const args: string[] = [];
    for (const arg of server.args) {
      const placeholderMatch = /^\{(.+)\}$/.exec(arg);
      if (placeholderMatch) {
        const key = placeholderMatch[1];
        const value = (finalValues[key] ?? "").trim();
        if (!value) {
          showToast(`${key} is required.`, "warning");
          return null;
        }
        args.push(value);
      } else {
        args.push(arg);
      }
    }

    const env: Record<string, string> = {};
    for (const field of server.env_fields) {
      const value = (finalValues[field.key] ?? "").trim();
      // Skip placeholder-style values (used in args); only real env vars go to env.
      const isPlaceholder = server.args.some(
        (arg) => arg === `{${field.key}}`,
      );
      if (isPlaceholder) continue;
      if (!value && field.required) {
        showToast(`${field.label || field.key} is required.`, "warning");
        return null;
      }
      if (value) {
        env[field.key] = value;
      }
    }

    return {
      name: name().trim() || server.id,
      transport: server.transport,
      command: server.command,
      args,
      env,
      url: server.url,
      headers: {},
      enabled: true,
    };
  };

  const handleSubmit = async () => {
    const payload = buildPayload();
    if (!payload) return;
    setSubmitting(true);
    try {
      await props.onSubmit(payload);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={props.open}
      title={props.server ? `Install ${props.server.name}` : "Install MCP server"}
      onClose={props.onClose}
      footer={
        <div class="row-wrap" style={{ "justify-content": "flex-end", width: "100%" }}>
          <button class="btn" type="button" onClick={props.onClose}>
            Cancel
          </button>
          <button
            class="btn btn-primary"
            type="button"
            onClick={handleSubmit}
            disabled={submitting()}
          >
            {submitting() ? "Saving..." : "Install"}
          </button>
        </div>
      }
    >
      <Show when={props.server}>
        {(serverGetter) => {
          const server = serverGetter();
          ensureForm();
          return (
            <div class="stack">
              <div class="hint">{server.description}</div>
              <Show when={server.homepage}>
                <a
                  class="mono"
                  style={{ "font-size": "12px" }}
                  href={server.homepage}
                  target="_blank"
                  rel="noreferrer"
                >
                  {server.homepage}
                </a>
              </Show>

              <div class="field">
                <label>Server name</label>
                <input
                  class="input mono"
                  placeholder={server.id}
                  value={name()}
                  onInput={(event) => setName(event.currentTarget.value)}
                />
              </div>

              <For each={server.env_fields}>
                {(field) => (
                  <div class="field">
                    <label>
                      {field.label}
                      <Show when={!field.required}>
                        <span class="hint"> (optional)</span>
                      </Show>
                    </label>
                    <input
                      class="input mono"
                      type={field.secret ? "password" : "text"}
                      value={values()[field.key] ?? ""}
                      onInput={(event) =>
                        setValues({
                          ...values(),
                          [field.key]: event.currentTarget.value,
                        })
                      }
                    />
                    <Show when={field.description}>
                      <div class="hint">{field.description}</div>
                    </Show>
                  </div>
                )}
              </For>
            </div>
          );
        }}
      </Show>
    </Dialog>
  );
}
