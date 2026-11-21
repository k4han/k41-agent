import { Search } from "lucide-solid";
import { JSX, Show } from "solid-js";

export type SettingsResourceToolbarProps = {
  searchValue?: string;
  searchPlaceholder?: string;
  onSearchInput?: (value: string) => void;
  filters?: JSX.Element;
  actions?: JSX.Element;
  children?: JSX.Element;
};

export function SettingsResourceToolbar(props: SettingsResourceToolbarProps) {
  return (
    <div class="settings-resource-toolbar">
      <Show when={props.searchValue !== undefined || props.onSearchInput}>
        <label class="settings-resource-search">
          <Search size={15} />
          <input
            type="search"
            aria-label={props.searchPlaceholder || "Search..."}
            placeholder={props.searchPlaceholder || "Search..."}
            value={props.searchValue ?? ""}
            onInput={(event) => props.onSearchInput?.(event.currentTarget.value)}
          />
        </label>
      </Show>
      <Show when={props.filters}>
        <div class="settings-resource-filters">{props.filters}</div>
      </Show>
      <Show when={props.children}>
        {props.children}
      </Show>
      <Show when={props.actions}>
        <div class="settings-resource-actions">{props.actions}</div>
      </Show>
    </div>
  );
}
