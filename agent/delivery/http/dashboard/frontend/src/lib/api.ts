type ApiInit = RequestInit & {
  json?: unknown;
};

export async function readError(response: Response): Promise<string> {
  const text = await response.text();
  if (!text) {
    return "Unknown error";
  }

  try {
    const data = JSON.parse(text) as { detail?: string; message?: string };
    return data.detail || data.message || text;
  } catch {
    return text;
  }
}

export async function apiFetch<T>(path: string, init: ApiInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("content-type", "application/json");
    headers.set("accept", "application/json");
    body = JSON.stringify(init.json);
  }

  const response = await fetch(path, {
    ...init,
    body,
    headers,
    credentials: "same-origin",
  });

  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Not authenticated");
  }

  if (response.redirected && new URL(response.url).pathname === "/login") {
    window.location.href = "/login";
    throw new Error("Not authenticated");
  }

  if (!response.ok) {
    throw new Error(await readError(response));
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function postJson<T>(path: string, json?: unknown): Promise<T> {
  return apiFetch<T>(path, { method: "POST", json });
}

export function putJson<T>(path: string, json?: unknown): Promise<T> {
  return apiFetch<T>(path, { method: "PUT", json });
}

export function patchJson<T>(path: string, json?: unknown): Promise<T> {
  return apiFetch<T>(path, { method: "PATCH", json });
}

export function deleteJson<T>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: "DELETE" });
}
