import { getCsrfToken } from "@/lib/api/client";
import { limited } from "@/lib/api/limiter";

/** AV1-engine (lvos04/av1) via core: /api/v1/encoder/* → engine /api/* (core controleert de
 * login en voegt het interne token toe; de browser praat nooit rechtstreeks met de engine). */
export const ENCODER_BASE = "/api/v1/encoder";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export function api<T>(path: string, init?: RequestInit): Promise<T> {
  return !init?.method || init.method === "GET" ? limited(() => apiNow<T>(path, init)) : apiNow<T>(path, init);
}

async function apiNow<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${ENCODER_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.method && init.method !== "GET" && getCsrfToken() ? { "X-CSRF-Token": getCsrfToken() as string } : {}),
      ...init?.headers,
    },
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) {
    const value: unknown = await response.json().catch(() => null);
    const rawDetail =
      typeof value === "object" && value !== null && "detail" in value
        ? value.detail
        : response.statusText;
    const detail = typeof rawDetail === "string" ? rawDetail : JSON.stringify(rawDetail);
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export function mutation<T>(path: string, method: string, body?: unknown): Promise<T> {
  return api<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
}
