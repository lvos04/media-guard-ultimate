"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";

export interface EncoderResource<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** Tijdstip (ms) van het laatst gelukte antwoord; bij een fout blijft de oude waarde staan. */
  fetchedAt: number | null;
  /** Er is data, maar de laatste verversing mislukte (bv. engine druk). */
  stale: boolean;
  reload: () => Promise<void>;
}

// Laatst bekende antwoorden per pad (hele app): een nieuwe pagina of een trage engine toont dan
// niet "Nog niet beschikbaar", maar de vorige waarde met "bijgewerkt X geleden" (R14).
const lastKnown = new Map<string, { value: unknown; at: number }>();

/** GET on the AV1 engine (via core: /api/v1/encoder/*) with error state and optional polling. */
export function useEncoder<T>(path: string | null, intervalMs?: number): EncoderResource<T> {
  const cached = path ? lastKnown.get(path) : undefined;
  const [data, setData] = useState<T | null>((cached?.value as T | undefined) ?? null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(cached?.at ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path) && !cached);
  const current = useRef(path);
  useEffect(() => { current.current = path; }, [path]);

  // nooit een nieuwe poll starten zolang de vorige nog loopt (trage /jobs stapelde op, M16)
  const inflight = useRef<Promise<void> | null>(null);
  const reload = useCallback(async () => {
    if (!path) return;
    if (inflight.current) return inflight.current;
    const run = (async () => { try {
      const value = await api<T>(path);
      if (current.current !== path) return;
      const at = Date.now();
      lastKnown.set(path, { value, at });
      setData(value);
      setFetchedAt(at);
      setError(null);
    } catch (e) {
      if (current.current !== path) return;
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      if (current.current === path) setLoading(false);
    } })();
    inflight.current = run.finally(() => { inflight.current = null; });
    return inflight.current;
  }, [path]);

  useEffect(() => {
    const known = path ? lastKnown.get(path) : undefined;
    if (known) { setData(known.value as T); setFetchedAt(known.at); }
    setLoading(Boolean(path) && !known);
    void reload();
    if (!intervalMs || !path) return;
    const id = window.setInterval(() => { if (!document.hidden) void reload(); }, intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs, path, reload]);

  return { data, error, loading, fetchedAt, stale: Boolean(error && data), reload };
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function fileName(path: string | null | undefined): string {
  if (!path) return "—";
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

export function folderOf(path: string | null | undefined): string {
  if (!path) return "";
  const i = path.lastIndexOf("/");
  return i > 0 ? path.slice(0, i) : "";
}
