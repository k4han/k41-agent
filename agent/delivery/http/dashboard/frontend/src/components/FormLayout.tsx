import { createMemo, createSignal, For, JSX, Show, splitProps } from "solid-js";
import { classNames } from "@/lib/utils";

export type FormGridProps = {
  columns?: number | "auto" | "responsive";
  gap?: number;
  class?: string;
  children: JSX.Element;
};

export function FormGrid(props: FormGridProps) {
  const [local] = splitProps(props, ["columns", "gap", "class", "children"]);

  const gridStyle = createMemo(() => {
    const gap = local.gap ?? 16;
    const columns = local.columns ?? "responsive";

    let gridTemplateColumns = "1fr";

    switch (columns) {
      case "auto":
        gridTemplateColumns = "repeat(auto-fit, minmax(200px, 1fr))";
        break;
      case "responsive":
        gridTemplateColumns = "repeat(auto-fit, minmax(250px, 1fr))";
        break;
      default:
        if (typeof columns === "number") {
          gridTemplateColumns = `repeat(${columns}, 1fr)`;
        }
        break;
    }

    return {
      gap: `${gap}px`,
      "grid-template-columns": gridTemplateColumns,
    };
  });

  const columnsClass = createMemo(() => {
    if (local.columns === "auto") {
      return "form-grid--auto";
    }
    if (local.columns === "responsive") {
      return "form-grid--responsive";
    }
    if (typeof local.columns === "number") {
      return `form-grid--${local.columns}`;
    }
    return "form-grid--responsive";
  });

  return (
    <div
      class={classNames("form-grid", columnsClass(), local.class)}
      style={gridStyle()}
    >
      {local.children}
    </div>
  );
}

export type FormSectionProps = {
  title?: string;
  description?: string;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  class?: string;
  children: JSX.Element;
};

export function FormSection(props: FormSectionProps) {
  const [local] = splitProps(props, [
    "title",
    "description",
    "collapsible",
    "defaultCollapsed",
    "class",
    "children",
  ]);

  const [collapsed, setCollapsed] = createSignal(local.defaultCollapsed ?? false);

  const toggleCollapse = () => {
    if (local.collapsible) {
      setCollapsed((prev: boolean) => !prev);
    }
  };

  return (
    <div class={classNames("form-section", local.class)}>
      <Show when={local.title}>
        <div
          class={classNames(
            "form-section-header",
            local.collapsible && "form-section-header--collapsible"
          )}
          onClick={toggleCollapse}
        >
          <div class="form-section-header-content">
            <h3 class="form-section-title">{local.title}</h3>
            <Show when={local.description}>
              <p class="form-section-description">{local.description}</p>
            </Show>
          </div>
          <Show when={local.collapsible}>
            <div class={classNames(
              "form-section-collapse-icon",
              collapsed() && "form-section-collapse-icon--collapsed"
            )}>
              ▼
            </div>
          </Show>
        </div>
      </Show>
      <Show when={!collapsed()}>
        <div class="form-section-body">
          {local.children}
        </div>
      </Show>
    </div>
  );
}

export type FormRowProps = {
  align?: "start" | "center" | "end" | "stretch";
  justify?: "start" | "center" | "end" | "between" | "around";
  gap?: number;
  class?: string;
  children: JSX.Element;
};

export function FormRow(props: FormRowProps) {
  const [local] = splitProps(props, ["align", "justify", "gap", "class", "children"]);

  const rowStyle = createMemo(() => {
    const gap = local.gap ?? 12;
    return {
      gap: `${gap}px`,
      "align-items": local.align ?? "start",
      "justify-content": local.justify ?? "start",
    };
  });

  return (
    <div
      class={classNames("form-row", local.class)}
      style={rowStyle()}
    >
      {local.children}
    </div>
  );
}

export type FormColumnProps = {
  width?: string | number;
  flex?: boolean;
  class?: string;
  children: JSX.Element;
};

export function FormColumn(props: FormColumnProps) {
  const [local] = splitProps(props, ["width", "flex", "class", "children"]);

  const columnStyle = createMemo(() => {
    const style: Record<string, string> = {};

    if (local.width !== undefined) {
      style.width = typeof local.width === "number" ? `${local.width}px` : local.width;
    }

    if (local.flex) {
      style.flex = "1";
      style["min-width"] = "0";
    }

    return style;
  });

  return (
    <div
      class={classNames("form-column", local.flex && "form-column--flex", local.class)}
      style={columnStyle()}
    >
      {local.children}
    </div>
  );
}

export type FormSpacerProps = {
  size?: number;
  class?: string;
};

export function FormSpacer(props: FormSpacerProps) {
  const size = () => props.size ?? 16;

  return (
    <div
      class={classNames("form-spacer", props.class)}
      style={{ height: `${size()}px` }}
    />
  );
}

export type FormDividerProps = {
  label?: string;
  class?: string;
};

export function FormDivider(props: FormDividerProps) {
  return (
    <div class={classNames("form-divider", props.class)}>
      <Show when={props.label}>
        <span class="form-divider-label">{props.label}</span>
      </Show>
    </div>
  );
}

export type FormActionsProps = {
  align?: "left" | "center" | "right";
  sticky?: boolean;
  class?: string;
  children: JSX.Element;
};

export function FormActions(props: FormActionsProps) {
  const [local] = splitProps(props, ["align", "sticky", "class", "children"]);

  const alignClass = createMemo(() => {
    switch (local.align) {
      case "center":
        return "form-actions--center";
      case "right":
        return "form-actions--right";
      default:
        return "form-actions--left";
    }
  });

  return (
    <div
      class={classNames(
        "form-actions",
        alignClass(),
        local.sticky && "form-actions--sticky",
        local.class
      )}
    >
      {local.children}
    </div>
  );
}

export type FormCardProps = {
  title?: string;
  description?: string;
  variant?: "default" | "compact" | "elevated";
  class?: string;
  children: JSX.Element;
};

export function FormCard(props: FormCardProps) {
  const [local] = splitProps(props, ["title", "description", "variant", "class", "children"]);

  const variantClass = createMemo(() => {
    switch (local.variant) {
      case "compact":
        return "form-card--compact";
      case "elevated":
        return "form-card--elevated";
      default:
        return "form-card--default";
    }
  });

  return (
    <div class={classNames("form-card", variantClass(), local.class)}>
      <Show when={local.title || local.description}>
        <div class="form-card-header">
          <Show when={local.title}>
            <h3 class="form-card-title">{local.title}</h3>
          </Show>
          <Show when={local.description}>
            <p class="form-card-description">{local.description}</p>
          </Show>
        </div>
      </Show>
      <div class="form-card-body">
        {local.children}
      </div>
    </div>
  );
}
