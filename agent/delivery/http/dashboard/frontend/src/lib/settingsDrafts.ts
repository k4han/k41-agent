// Temporary in-memory drafts while creating a connection for another form.
const drafts = new Map<string, unknown>();

export function keepSettingsDraft<T>(key: string, value: T) {
  drafts.set(key, structuredClone(value));
}

export function takeSettingsDraft<T>(key: string): T | undefined {
  const value = drafts.get(key) as T | undefined;
  drafts.delete(key);
  return value;
}
