// Temporary in-memory drafts while creating a connection for another form.
const drafts = new Map<string, unknown>();

function cloneDraft<T>(value: T): T {
  if (value === undefined) {
    return value;
  }
  try {
    if (typeof structuredClone === "function") {
      return structuredClone(value);
    }
  } catch {
    // Fall through to JSON clone for values structuredClone cannot handle.
  }
  return JSON.parse(JSON.stringify(value ?? null)) as T;
}

export function keepSettingsDraft<T>(key: string, value: T) {
  drafts.set(key, cloneDraft(value));
}

export function takeSettingsDraft<T>(key: string): T | undefined {
  const value = drafts.get(key) as T | undefined;
  drafts.delete(key);
  return value;
}
