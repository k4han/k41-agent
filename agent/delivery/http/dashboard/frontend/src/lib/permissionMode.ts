export type PermissionMode = "ask" | "auto" | "full_access";

export const PERMISSION_OPTIONS = [
  { value: "", label: "Inherit permissions" },
  { value: "ask", label: "Ask for approval" },
  { value: "auto", label: "Approve for me" },
  { value: "full_access", label: "Full access" },
];

export const ACP_PERMISSION_LIMIT = "K41 enforces permissions on its own tools and native approval requests forwarded by the ACP provider. Native operations without a forwarded request are outside K41 enforcement; these modes do not sandbox the external agent.";
export const FULL_ACCESS_NOTICE = "Full access removes approval prompts for K41 tools and forwarded ACP requests. Explicit deny rules and hard blocks still apply within K41 enforcement.";

export function readPermissionMode(storage: Pick<Storage, "getItem">, scope: string): PermissionMode | undefined {
  try {
    const value = storage.getItem(`k41-permission-mode:${scope}`);
    return value === "ask" || value === "auto" || value === "full_access" ? value : undefined;
  } catch {
    return undefined;
  }
}

export function savePermissionMode(storage: Pick<Storage, "setItem" | "removeItem">, scope: string, mode: PermissionMode | undefined): void {
  try {
    const key = `k41-permission-mode:${scope}`;
    if (mode) storage.setItem(key, mode);
    else storage.removeItem(key);
  } catch {
    // The current conversation can still use the selection without browser storage.
  }
}
