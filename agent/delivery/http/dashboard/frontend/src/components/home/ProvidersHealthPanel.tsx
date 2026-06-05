import { For, Show } from "solid-js";
import { A } from "@solidjs/router";

import { truncateText } from "@/lib/utils";
import type { ProviderHealth } from "@/types";

export function ProvidersHealthPanel(props: { providers: ProviderHealth[] }) {
  return (
    <section class="panel">
      <div class="panel-header split">
        <div>
          <div class="panel-title">LLM providers</div>
          <div class="panel-subtitle">
            Configured providers and their default model.
          </div>
        </div>
        <A class="btn btn-sm" href="/settings/providers">
          Manage
        </A>
      </div>
      <div class="panel-body">
        <Show
          when={props.providers.length > 0}
          fallback={<div class="empty">No LLM providers configured.</div>}
        >
          <ul class="provider-list">
            <For each={props.providers}>
              {(provider) => (
                <li class="provider-row">
                  <div class="provider-row-main">
                    <div class="provider-row-name">
                      <span>{provider.name}</span>
                      <span class="muted provider-row-type">{provider.type || "—"}</span>
                    </div>
                    <div class="muted provider-row-meta">
                      {provider.default_model
                        ? truncateText(provider.default_model, 48)
                        : "No default model"}
                    </div>
                  </div>
                  <ProviderStatus provider={provider} />
                </li>
              )}
            </For>
          </ul>
        </Show>
      </div>
    </section>
  );
}

function ProviderStatus(props: { provider: ProviderHealth }) {
  if (props.provider.ready) {
    return <span class="badge badge-success">Ready</span>;
  }
  if (!props.provider.enabled) {
    return <span class="badge">Disabled</span>;
  }
  if (!props.provider.has_api_key) {
    return <span class="badge badge-warning">No API key</span>;
  }
  return <span class="badge badge-warning">Setup incomplete</span>;
}
