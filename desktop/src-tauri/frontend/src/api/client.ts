import { getApiBase } from "../api/base";

export async function apiFetch(path: string, options: RequestInit = {}) {
  const res = await fetch(`${getApiBase}${path}`, {
    ...options,
  });

  if (!res.ok) {
    throw new Error(`API error ${res.status}`);
  }

  return res.json();
}
