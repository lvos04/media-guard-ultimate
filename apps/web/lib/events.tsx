"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { CORE_BASE } from "@/lib/api/client";
import type { CoreEvent } from "@/lib/api/contracts";

type Listener = (event: CoreEvent) => void;
export type LiveStatus = "connecting" | "live" | "reconnecting" | "offline";

interface EventsValue {
  status: LiveStatus;
  subscribe: (topicPrefix: string, listener: Listener) => () => void;
}

const EventsContext = createContext<EventsValue | null>(null);

type Wire =
  | { type: "event"; data: string; id: string | null }
  | { type: "status"; status: LiveStatus }
  | { type: "hello" };

/**
 * Eén SSE-verbinding voor álle tabbladen samen (H5). Browsers staan bij HTTP/1.1 maar zes
 * verbindingen per host toe; met een open stream per tab liepen andere verzoeken bij 2–3
 * tabbladen vast (tot 44 s). Eén tabblad wordt 'leider' (Web Locks) en houdt de stream open;
 * de rest krijgt de events via BroadcastChannel. Sluit de leider, dan neemt een ander het over
 * (met last_id, zodat core gemiste events opnieuw stuurt). Zonder Web Locks/BroadcastChannel
 * valt het terug op één eigen verbinding per tab.
 * EventSource re-sends Last-Event-ID automatically on its own retries; when the browser gives up
 * (readyState CLOSED, e.g. after a 502) we reconnect with exponential backoff + jitter.
 */
export function EventsProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const listeners = useRef(new Map<Listener, string>());
  const lastId = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") return;
    let source: EventSource | null = null;
    let timer: number | undefined;
    let attempt = 0;
    let stopped = false;
    let leader = false;
    let currentStatus: LiveStatus = "connecting";
    const shared = typeof BroadcastChannel !== "undefined" && typeof navigator !== "undefined" && Boolean(navigator.locks);
    const channel = shared ? new BroadcastChannel("mg-events") : null;
    const abort = new AbortController();
    let releaseLock: (() => void) | null = null;

    const dispatch = (raw: string, id: string | null) => {
      if (id) lastId.current = id;
      let event: CoreEvent;
      try {
        event = JSON.parse(raw) as CoreEvent;
      } catch {
        return;
      }
      listeners.current.forEach((prefix, listener) => {
        if (!prefix || event.topic?.startsWith(prefix)) listener(event);
      });
    };
    const announce = (next: LiveStatus) => {
      currentStatus = next;
      setStatus(next);
      if (leader) channel?.postMessage({ type: "status", status: next } satisfies Wire);
    };

    const connect = () => {
      if (stopped) return;
      source?.close();
      const url = `${CORE_BASE}/events/stream${lastId.current ? `?last_id=${encodeURIComponent(lastId.current)}` : ""}`;
      source = new EventSource(url, { withCredentials: true });
      source.onopen = () => {
        attempt = 0;
        announce("live");
      };
      source.onmessage = (message) => {
        const id = message.lastEventId || null;
        dispatch(message.data, id);
        channel?.postMessage({ type: "event", data: message.data, id } satisfies Wire);
      };
      source.onerror = () => {
        if (source?.readyState === EventSource.CLOSED) {
          source.close();
          announce(attempt > 3 ? "offline" : "reconnecting");
          const delay = Math.min(30_000, 1000 * 2 ** attempt) + Math.random() * 500;
          attempt += 1;
          timer = window.setTimeout(connect, delay);
        } else {
          announce("reconnecting");
        }
      };
    };

    if (channel) {
      channel.onmessage = (e: MessageEvent<Wire>) => {
        const msg = e.data;
        if (msg.type === "event" && !leader) dispatch(msg.data, msg.id);
        else if (msg.type === "status" && !leader) { currentStatus = msg.status; setStatus(msg.status); }
        else if (msg.type === "hello" && leader) channel.postMessage({ type: "status", status: currentStatus } satisfies Wire);
      };
      channel.postMessage({ type: "hello" } satisfies Wire);
      // wacht tot dit tabblad leider mag worden; de lock blijft vast tot het tabblad sluit
      navigator.locks.request("mg-events-leader", { signal: abort.signal }, () => new Promise<void>((resolve) => {
        if (stopped) { resolve(); return; }
        leader = true;
        releaseLock = resolve;
        connect();
      })).catch(() => undefined);
    } else {
      leader = true;
      connect();
    }
    return () => {
      stopped = true;
      abort.abort();
      if (timer) window.clearTimeout(timer);
      source?.close();
      releaseLock?.();
      channel?.close();
    };
  }, [enabled]);

  // Stabiel: alleen een statuswissel (live/offline) maakt een nieuwe waarde. Elk event via
  // context doorgeven liet de hele shell en alle pagina's opnieuw renderen.
  const subscribe = useCallback((prefix: string, listener: Listener) => {
    listeners.current.set(listener, prefix);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);
  const value = useMemo<EventsValue>(() => ({ status, subscribe }), [status, subscribe]);

  return <EventsContext.Provider value={value}>{children}</EventsContext.Provider>;
}

export function useLiveStatus(): LiveStatus {
  return useContext(EventsContext)?.status ?? "offline";
}

/** Subscribe to topics starting with `prefix` (empty = all). Listener may change identity freely. */
export function useCoreEvents(prefix: string, listener: Listener): void {
  const subscribe = useContext(EventsContext)?.subscribe;
  const ref = useRef(listener);
  useEffect(() => {
    ref.current = listener;
  });
  useEffect(() => {
    if (!subscribe) return;
    return subscribe(prefix, (event) => ref.current(event));
  }, [subscribe, prefix]);
}
