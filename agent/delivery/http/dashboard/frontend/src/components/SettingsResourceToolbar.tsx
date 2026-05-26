import { Search } from "lucide-solid";
import { JSX, Show } from "solid-js";

export type SettingsResourceToolbarProps = {
  searchValue: string;
  searchPlaceholder: string;
  actions?: JSX.Element;
  onSearchInput: (value: string) => void;
};

export function SettingsResourceToolbar(props: SettingsResourceToolbarProps) {
  return (
    <div class="settings-resource-toolbar">
      <label class="settings-resource-search">
        <Search size={15} />
        <input
          type="search"
          aria-label={props.searchPlaceholder}
          placeholder={props.searchPlaceholder}
          value={props.searchValue}
          onInput={(event) => props.onSearchInput(event.currentTarget.value)}
        />
      </label>
      <Show when={props.actions}>
        <div class="settings-resource-actions">{props.actions}</div>
      </Show>
    </div>
  );
}
