"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { useLiveStatus } from "@/lib/events";

import { ApiError, coreRequest, onMutation, type RequestOptions } from "./client";

export type ResourceState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "ok"; data: T; error: null }
  | { status: "error"; data: T | null; error: ApiError };

export interface Resource<T> {
  state: ResourceState<T>;
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  /** True when we have data but the last refresh failed (stale view). */
  stale: boolean;
  fetchedAt: number | null;
  reload: () => Promise<void>;
  setData: (updater: (old: T | null) => T | null) => void;
}

function asApiError(error: unknown, path: string): ApiError {
  if (error instanceof ApiError) return error;
  return new ApiError("server", 0, error instanceof Error ? error.message : String(error), path);
}

// ---------------------------------------------------------------- gedeelde cache
// Eén entry per pad+query voor de hele app: twee componenten die hetzelfde vragen delen één
// request; een tweede pagina binnen FRESH_MS hergebruikt het antwoord. reload() tijdens een
// lopend request plant precies één vervolgrequest (nooit een stroom).
interface Entry {
  data: unknown;
  error: ApiError | null;
  fetchedAt: number | null;
  inflight: Promise<void> | null;
  again: boolean;
  subs: Set<() => void>;
}
const FRESH_MS = 15_000;
const LIVE_FRESH_MS = 90_000;
const store = new Map<string, Entry>();

function entryFor(key: string): Entry {
  let e = store.get(key);
  if (!e) {
    e = { data: null, error: null, fetchedAt: null, inflight: null, again: false, subs: new Set() };
    store.set(key, e);
  }
  return e;
}

function fetchEntry(key: string, path: string, query: RequestOptions["query"], force = false): Promise<void> {
  const e = entryFor(key);
  if (e.inflight) {
    // een nieuw onderdeel dat hetzelfde vraagt, wacht gewoon mee; alleen een expliciete
    // reload() (na een event/mutatie) vraagt daarna nog één keer op
    if (force) e.again = true;
    return e.inflight;
  }
  const run = async (): Promise<void> => {
    try {
      e.data = await coreRequest<unknown>(path, { query });
      e.error = null;
      e.fetchedAt = Date.now();
    } catch (error) {
      e.error = asApiError(error, path);
    }
    e.subs.forEach((fn) => fn());
    if (e.again) {
      e.again = false;
      await run();
    }
  };
  e.inflight = run().finally(() => { e.inflight = null; });
  return e.inflight;
}

/** Na een mutatie: alle gecachte antwoorden onder dit pad als verouderd markeren. */
export function invalidate(prefix = ""): void {
  store.forEach((e, key) => { if (key.startsWith(prefix)) e.fetchedAt = null; });
}
onMutation(() => invalidate());

function useVisibleTab(): boolean {
  const [visible, setVisible] = useState(() => typeof document === "undefined" || !document.hidden);
  useEffect(() => {
    const on = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return visible;
}

export interface ResourceOptions {
  /** Poll trager zolang de live-verbinding (SSE) werkt: events sturen de verversing al aan. */
  liveSlowdown?: boolean;
}

/**
 * Load a core resource. `path = null` disables the request (e.g. waiting for a parameter).
 * `refreshMs` re-polls (paused while the tab is hidden, caught up when it becomes visible);
 * SSE-driven pages call `reload()` on matching topics instead.
 */
export function useResource<T>(path: string | null, query?: RequestOptions["query"], refreshMs?: number, options?: ResourceOptions): Resource<T> {
  const queryKey = JSON.stringify(query ?? {});
  const key = path ? `${path}?${queryKey}` : null;
  const [, force] = useState(0);
  const visible = useVisibleTab();
  const live = useLiveStatus() === "live";
  const parsedQuery = useMemo(() => JSON.parse(queryKey) as RequestOptions["query"], [queryKey]);

  useEffect(() => {
    if (!key) return;
    const e = entryFor(key);
    const notify = () => force((n) => n + 1);
    e.subs.add(notify);
    // met een werkende live-verbinding sturen events de verversing al aan: een paginawissel
    // hoeft dezelfde globale gegevens dan niet opnieuw op te halen (N25)
    const fresh = live ? LIVE_FRESH_MS : FRESH_MS;
    if (e.fetchedAt === null || Date.now() - e.fetchedAt > fresh) void fetchEntry(key, path!, parsedQuery);
    else notify();
    return () => { e.subs.delete(notify); };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- live alleen bij het aankoppelen
  }, [key, path, parsedQuery]);

  const every = refreshMs ? (options?.liveSlowdown && live ? Math.max(refreshMs * 4, 60_000) : refreshMs) : 0;
  useEffect(() => {
    if (!every || !key || !visible) return;
    const e = entryFor(key);
    // na terugkomen meteen bijwerken als de laatste ophaling te oud is
    if (e.fetchedAt !== null && Date.now() - e.fetchedAt > every) void fetchEntry(key, path!, parsedQuery);
    const id = window.setInterval(() => void fetchEntry(key, path!, parsedQuery), every);
    return () => window.clearInterval(id);
  }, [every, key, path, parsedQuery, visible]);

  const reload = useCallback(async () => {
    if (!key) return;
    await fetchEntry(key, path!, parsedQuery, true);
  }, [key, path, parsedQuery]);

  const setData = useCallback((updater: (old: T | null) => T | null) => {
    if (!key) return;
    const e = entryFor(key);
    const next = updater(e.data as T | null);
    if (next === null) return;
    e.data = next;
    e.error = null;
    e.subs.forEach((fn) => fn());
  }, [key]);

  const e = key ? store.get(key) : undefined;
  const data = (e?.data ?? null) as T | null;
  const error = e?.error ?? null;
  const state: ResourceState<T> = data !== null && !error ? { status: "ok", data, error: null }
    : error ? { status: "error", data, error }
      : { status: "loading", data: null, error: null };
  return {
    state,
    data,
    error,
    loading: state.status === "loading",
    stale: state.status === "error" && data !== null,
    fetchedAt: e?.fetchedAt ?? null,
    reload,
    setData,
  };
}

export interface Mutation<A extends unknown[], R> {
  run: (...args: A) => Promise<R | undefined>;
  pending: boolean;
  error: ApiError | null;
  reset: () => void;
}

/** Wrap a mutation: tracks pending/error; never throws (returns undefined on failure). */
export function useMutation<A extends unknown[], R>(fn: (...args: A) => Promise<R>): Mutation<A, R> {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const run = useCallback(async (...args: A) => {
    setPending(true);
    setError(null);
    try {
      return await fn(...args);
    } catch (e) {
      setError(asApiError(e, "mutation"));
      return undefined;
    } finally {
      setPending(false);
    }
  }, [fn]);
  return { run, pending, error, reset: () => setError(null) };
}
