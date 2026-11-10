"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { EventMessage, LiveWorker, TickMessage } from "@/types";
import { parseServerDate } from "@/lib/i18n";

type Metric = {
  tijd: string;
  cpu: number;
  gpuVideo: number | null;
  gpuRender: number | null;
  gpuPower: number | null;
  packagePower: number | null;
  platformPower: number | null;
  memoryPercent: number;
  memoryUsedBytes: number;
  /** deel van het werkgeheugen dat (ZFS-)cache is, als de engine dat meet (rapport 6) */
  memoryCachePercent?: number | null;
};

interface LiveState {
  tick: TickMessage | null;
  verbonden: boolean;
  events: EventMessage[];
  volg: (jobId: number) => void;
  stopVolgen: (jobId: number) => void;
}

/** Metrics live in their own context and are published at most every 5 s (the engine samples
 * every 5 s anyway): the 1 Hz tick no longer re-renders charts, and pages without a chart never
 * download the 1-hour history. */
interface MetricsState { metrics: Metric[]; want: () => void }
const MetricsContext = createContext<MetricsState | null>(null);
const METRIC_PUBLISH_MS = 5_000;

const LiveContext = createContext<LiveState | null>(null);
let lastTick: TickMessage | null = null;

function websocketUrl(): string {
  if (process.env.NEXT_PUBLIC_WS_URL) return process.env.NEXT_PUBLIC_WS_URL;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/v1/encoder/ws`;
}

function chartTime(value: string): string {
  const parsed = parseServerDate(value) ?? new Date(Number.NaN);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleTimeString("nl-NL", {
    timeZone: "Europe/Amsterdam",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function mergeWorkers(previous: LiveWorker[], current: LiveWorker[]): LiveWorker[] {
  return current.map((worker) => {
    const old = previous.find(
      (candidate) => candidate.baan === worker.baan && candidate.job_id === worker.job_id,
    );
    // Nieuwe fase = nieuwe meting: oude waarden niet overnemen (anders springt de balk terug
    // naar het eindpercentage van de vorige stap).
    if (!old || worker.job_id === null || (old.fase ?? null) !== (worker.fase ?? null)) return worker;
    const liveKeys: Array<keyof LiveWorker> = [
      "percent",
      "fps",
      "speed",
      "eta_seconden",
      "bitrate_kbps",
      "grootte_nu",
    ];
    const merged = { ...worker };
    let stale = false;
    for (const key of liveKeys) {
      // only "stale" when the engine had a value in this phase and suddenly reports none
      // (phases without a percentage, like verification, are not stale)
      if ((worker[key] === null || worker[key] === undefined) && old[key] !== null && old[key] !== undefined) {
        Object.assign(merged, { [key]: old[key] });
        stale = true;
      }
    }
    return { ...merged, verouderd: stale };
  });
}

export function LiveProvider({ children }: { children: ReactNode }) {
  // laatst bekende stand over paginawissels heen: geen lege kaarten zolang de verbinding opbouwt
  const [tick, setTick] = useState<TickMessage | null>(() => lastTick);
  const [verbonden, setVerbonden] = useState(false);
  const [events, setEvents] = useState<EventMessage[]>([]);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const buffer = useRef<Metric[]>([]);
  const historyWanted = useRef(false);
  const lastPublish = useRef(0);
  const publish = useCallback((force = false) => {
    const now = Date.now();
    if (!force && now - lastPublish.current < METRIC_PUBLISH_MS) return;
    lastPublish.current = now;
    setMetrics(buffer.current.slice());
  }, []);
  const want = useCallback(() => {
    if (historyWanted.current) return;
    historyWanted.current = true;
    // stap=60: minuutgemiddelden (60 punten i.p.v. 720); oudere engines negeren de parameter
    fetch("/api/v1/encoder/systeem/metingen?uren=1&stap=60", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : [])
      .then((value: unknown) => {
        if (!Array.isArray(value)) return;
        const history = value.map((item) => {
          const metric = item as Metric;
          return { ...metric, tijd: chartTime(metric.tijd) };
        });
        buffer.current = [...history, ...buffer.current].slice(-720);
        publish(true);
      })
      .catch(() => undefined);
  }, [publish]);
  const socket = useRef<WebSocket | null>(null);
  const channelRef = useRef<BroadcastChannel | null>(null);
  const subscriptions = useRef(new Set<number>());
  const notificationPreferences = useRef({ fouten: false, batches: false });

  useEffect(() => {
    let stopped = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    fetch("/api/v1/encoder/instellingen", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((value: unknown) => {
        if (typeof value === "object" && value !== null) {
          const preferences = value as Record<string, unknown>;
          notificationPreferences.current = {
            fouten: preferences.meld_browser_fouten === true,
            batches: preferences.meld_browser_batches === true,
          };
        }
      })
      .catch(() => undefined);

    // Eén websocket voor alle tabbladen (H5): het zichtbare leider-tabblad houdt hem open en
    // deelt elk bericht via BroadcastChannel; andere tabbladen sturen hun 'volg'-opdrachten via
    // de leider. Een verborgen leider geeft het stokje door en sluit zijn verbinding.
    const shared = typeof BroadcastChannel !== "undefined" && typeof navigator !== "undefined" && Boolean(navigator.locks);
    const channel = shared ? new BroadcastChannel("mg-encoder-ws") : null;
    let leader = false;
    let lockAbort: AbortController | null = null;
    let releaseLock: (() => void) | null = null;
    channelRef.current = channel;

    const handle = (raw: string) => {
      let value: TickMessage | EventMessage;
      try { value = JSON.parse(raw) as TickMessage | EventMessage; } catch { return; }
      if (value.type === "tick") {
        setTick((old) => {
          const next = { ...value, workers: mergeWorkers(old?.workers ?? [], value.workers) };
          lastTick = next;
          return next;
        });
        buffer.current.push({
            tijd: chartTime(new Date().toISOString()),
            cpu: value.systeem.cpu_percent,
            gpuVideo: value.systeem.gpu.beschikbaar
              ? value.systeem.gpu.video_percent : null,
            gpuRender: value.systeem.gpu.beschikbaar
              ? value.systeem.gpu.render_percent : null,
            gpuPower: value.systeem.gpu.power_w,
            packagePower: value.systeem.gpu.package_power_w,
            platformPower: value.systeem.gpu.platform_power_w,
            memoryPercent: value.systeem.geheugen?.percent ?? 0,
            memoryUsedBytes: value.systeem.geheugen?.gebruikt_bytes ?? 0,
            memoryCachePercent: cachePercent(value.systeem.geheugen),
        });
        if (buffer.current.length > 900) buffer.current = buffer.current.slice(-720);
        if (historyWanted.current) publish();
      } else {
        setEvents((old) => [...old.slice(-99), value]);
        // browsermelding alleen in het leider-tabblad: anders krijg je hem per tabblad
        if (!leader) return;
        try {
          if ("Notification" in window && Notification.permission === "granted") {
            if (value.type === "job_mislukt" && notificationPreferences.current.fouten) {
              new Notification("AV1 Pipeline: job mislukt", { body: value.reden });
            } else if (value.type === "job_klaar" && notificationPreferences.current.batches) {
              new Notification("AV1 Pipeline: job afgerond", { body: `Job #${value.job_id} is veilig geverifieerd.` });
            }
          }
        } catch {
          // Geweigerde of defecte browsermeldingen mogen live bediening nooit breken.
        }
      }
    };
    const announce = (up: boolean) => {
      setVerbonden(up);
      if (leader) channel?.postMessage({ type: "status", verbonden: up });
    };

    const connect = () => {
      if (stopped || !leader || document.hidden) return;
      const connection = new WebSocket(websocketUrl());
      socket.current = connection;
      connection.addEventListener("open", () => {
        retry = 0;
        announce(true);
        for (const jobId of subscriptions.current) {
          connection.send(JSON.stringify({ actie: "volg", job_id: jobId }));
        }
      });
      connection.addEventListener("message", (message) => {
        const raw = String(message.data);
        handle(raw);
        channel?.postMessage({ type: "msg", data: raw });
      });
      connection.addEventListener("close", () => {
        announce(false);
        if (socket.current === connection) socket.current = null;
        if (!stopped && leader && !document.hidden) {
          const delay = Math.min(30_000, 1_000 * 2 ** retry) + Math.random() * 500;
          retry += 1;
          timer = setTimeout(connect, delay);
        }
      });
      connection.addEventListener("error", () => connection.close());
    };

    const becomeLeader = () => {
      if (!channel) { leader = true; connect(); return; }
      if (lockAbort || document.hidden || stopped) return;
      lockAbort = new AbortController();
      navigator.locks.request("mg-encoder-ws-leader", { signal: lockAbort.signal }, () => new Promise<void>((resolve) => {
        if (stopped || document.hidden) { resolve(); lockAbort = null; return; }
        leader = true;
        releaseLock = () => { leader = false; resolve(); };
        connect();
      })).catch(() => { lockAbort = null; });
    };
    const resign = () => {
      if (timer) clearTimeout(timer);
      lockAbort?.abort();
      lockAbort = null;
      const wasLeader = leader;
      releaseLock?.();
      releaseLock = null;
      socket.current?.close();
      socket.current = null;
      if (wasLeader) channel?.postMessage({ type: "status", verbonden: false });
      if (!channel) leader = false;
    };

    if (channel) {
      channel.onmessage = (e: MessageEvent<{ type: string; data?: string; verbonden?: boolean }>) => {
        const msg = e.data;
        if (msg.type === "msg" && !leader && typeof msg.data === "string") handle(msg.data);
        else if (msg.type === "status" && !leader) {
          setVerbonden(Boolean(msg.verbonden));
          // nieuwe leider: eigen volg-opdrachten opnieuw aanbieden
          if (msg.verbonden) for (const jobId of subscriptions.current) channel.postMessage({ type: "cmd", data: JSON.stringify({ actie: "volg", job_id: jobId }) });
        } else if (msg.type === "cmd" && leader && typeof msg.data === "string") {
          if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(msg.data);
        } else if (msg.type === "hello" && leader) {
          channel.postMessage({ type: "status", verbonden: socket.current?.readyState === WebSocket.OPEN });
        }
      };
      channel.postMessage({ type: "hello" });
    }
    const onVisibility = () => {
      if (document.hidden) resign();
      else { retry = 0; becomeLeader(); }
    };
    document.addEventListener("visibilitychange", onVisibility);
    becomeLeader();
    return () => {
      stopped = true;
      document.removeEventListener("visibilitychange", onVisibility);
      resign();
      channel?.close();
      channelRef.current = null;
    };
  }, [publish]);

  const send = useCallback((payload: string) => {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(payload);
    else channelRef.current?.postMessage({ type: "cmd", data: payload });
  }, []);

  const volg = useCallback((jobId: number) => {
    subscriptions.current.add(jobId);
    send(JSON.stringify({ actie: "volg", job_id: jobId }));
  }, [send]);

  const stopVolgen = useCallback((jobId: number) => {
    subscriptions.current.delete(jobId);
    send(JSON.stringify({ actie: "stop_volgen", job_id: jobId }));
  }, [send]);

  const value = useMemo(
    () => ({ tick, verbonden, events, volg, stopVolgen }),
    [events, stopVolgen, tick, verbonden, volg],
  );
  const metricsValue = useMemo(() => ({ metrics, want }), [metrics, want]);
  return (
    <LiveContext.Provider value={value}>
      <MetricsContext.Provider value={metricsValue}>{children}</MetricsContext.Provider>
    </LiveContext.Provider>
  );
}

/** Systeemmetingen voor grafieken; laadt de uurhistorie pas bij het eerste gebruik. */
export function useLiveMetrics(): Metric[] {
  const context = useContext(MetricsContext);
  if (!context) throw new Error("useLiveMetrics moet binnen LiveProvider worden gebruikt");
  const { want } = context;
  useEffect(() => { want(); }, [want]);
  return context.metrics;
}

export function useLive(): LiveState {
  const context = useContext(LiveContext);
  if (!context) throw new Error("useLive moet binnen LiveProvider worden gebruikt");
  return context;
}

function cachePercent(g: unknown): number | null {
  const m = g as { totaal_bytes?: number; cache_bytes?: number; zfs_arc_bytes?: number; zfs_cache_bytes?: number | null; cache_percent?: number } | null | undefined;
  if (!m) return null;
  if (typeof m.cache_percent === "number") return m.cache_percent;
  const cache = m.zfs_cache_bytes ?? m.zfs_arc_bytes ?? m.cache_bytes;
  return typeof cache === "number" && m.totaal_bytes ? Math.round((1000 * cache) / m.totaal_bytes) / 10 : null;
}
