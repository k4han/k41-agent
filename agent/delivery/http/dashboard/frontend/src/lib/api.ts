type ApiInit = RequestInit & {
  json?: unknown;
};

const CSRF_HEADER_NAME = "X-CSRF-Token";
const CSRF_BOOTSTRAP_PATH = "/dashboard-api/catalog";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

let csrfToken: string | null = null;
let csrfTokenRequest: Promise<void> | null = null;

function updateCsrfToken(response: Response): void {
  const serverCsrfToken = response.headers.get(CSRF_HEADER_NAME);
  if (serverCsrfToken) {
    csrfToken = serverCsrfToken;
  }
}

function requestMethod(input: RequestInfo | URL, init: RequestInit): string {
  if (init.method) {
    return init.method.toUpperCase();
  }
  if (input instanceof Request) {
    return input.method.toUpperCase();
  }
  return "GET";
}

async function ensureCsrfToken(): Promise<void> {
  if (csrfToken) {
    return;
  }

  csrfTokenRequest ??= fetch(CSRF_BOOTSTRAP_PATH, {
    method: "GET",
    headers: { accept: "application/json" },
    credentials: "same-origin",
  })
    .then(updateCsrfToken)
    .finally(() => {
      csrfTokenRequest = null;
    });

  await csrfTokenRequest;
}

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

  const response = await fetchWithCsrf(path, {
    ...init,
    body,
    headers,
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

export async function fetchWithCsrf(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const method = requestMethod(input, init);
  const headers = new Headers(init.headers);

  if (UNSAFE_METHODS.has(method)) {
    await ensureCsrfToken();
    if (csrfToken) {
      headers.set(CSRF_HEADER_NAME, csrfToken);
    }
  }

  const response = await fetch(input, {
    ...init,
    headers,
    credentials: init.credentials ?? "same-origin",
  });
  updateCsrfToken(response);
  return response;
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
