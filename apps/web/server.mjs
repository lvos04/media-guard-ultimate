// MediaGuard web server: serves the Next.js UI and proxies API traffic so the browser only ever
// talks to one origin (cookies stay HttpOnly/SameSite=strict, no secrets in the browser).
//
//   /api/v1/*         → MG_CORE_URL     (default http://localhost:8100)   — MediaGuard core (FastAPI)
//   /api/encoder/*    → MG_CORE_URL/api/v1/encoder/* — AV1-engine via core (login + intern token)
//
// Streaming responses (SSE: /api/v1/events/stream) are piped without buffering.
import { existsSync, readFileSync } from "node:fs";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { createServer } from "node:http";
import { connect as netConnect } from "node:net";
import { connect as tlsConnect } from "node:tls";
import zlib from "node:zlib";

import next from "next";

const host = process.env.MG_WEB_HOST ?? process.env.LAN_LISTEN_HOST ?? "0.0.0.0";
const port = Number.parseInt(process.env.PORT ?? "3000", 10);
const coreUrl = new URL(process.env.MG_CORE_URL ?? "http://localhost:8100");
const development = process.env.NODE_ENV !== "production";

const HOP_BY_HOP = new Set(["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
  "trailer", "transfer-encoding", "upgrade", "host"]);
// Nooit van de browser/tunnel doorgeven: interne sleutels (alleen voor diensten binnen de
// container) en de headers waarmee deze server core vertelt wie er aanbelde.
const STRIP = new Set(["x-mediaguard-token", "x-mediaguard-hook-key", "x-mediaguard-gateway", "x-mg-peer", "x-mg-tls"]);

function target(pathname) {
  if (pathname === "/api/v1" || pathname.startsWith("/api/v1/")) return { base: coreUrl, path: pathname };
  // AV1-engine alleen via core (login + intern token): oude /api/encoder/*-paden blijven werken
  if (pathname.startsWith("/api/encoder/")) return { base: coreUrl, path: "/api/v1/encoder/" + pathname.slice("/api/encoder/".length) };
  return null;
}

function forwardHeaders(req, base) {
  const headers = {};
  for (const [key, value] of Object.entries(req.headers)) {
    const k = key.toLowerCase();
    if (!HOP_BY_HOP.has(k) && !STRIP.has(k) && value !== undefined) headers[key] = value;
  }
  headers.host = base.host;
  headers["x-forwarded-host"] = req.headers.host ?? "";
  // Wie belde aan (TCP-adres) en via TLS? Core beslist zelf of hij CF-Connecting-IP en
  // X-Forwarded-Proto/-For (ongewijzigd doorgegeven) gelooft: alleen van een vertrouwde proxy
  // zoals de lokale cloudflared (mediaguard/security/net.py).
  headers["x-mg-peer"] = req.socket.remoteAddress ?? "";
  if (req.socket.encrypted) headers["x-mg-tls"] = "1";
  return headers;
}

// Core onbereikbaar: de AIO-supervisor schrijft in supervisor.json waarom (migratie bezig,
// herstartlus met uitleg zoals een volle /config). Dat tonen we in plaats van alleen 'offline'.
const supervisorState = process.env.MG_SUPERVISOR_STATE ?? "";

function coreDownDetail() {
  const fallback = { detail: "Upstream service unreachable", code: "upstream_unreachable" };
  let core;
  try {
    core = supervisorState ? JSON.parse(readFileSync(supervisorState, "utf8"))?.services?.core : null;
  } catch {
    return fallback;
  }
  if (!core || typeof core !== "object") return fallback;
  if (core.state === "migrating" || core.migrating) {
    const since = core.migrating?.since ? ` (sinds ${String(core.migrating.since).slice(11, 16)} UTC)` : "";
    return { detail: `MediaGuard werkt de database bij na de update${since}. Even geduld; niet herstarten.`, code: "core_migrating" };
  }
  if (typeof core.loop_nl === "string" && core.loop_nl) {
    return { detail: `MediaGuard-core herstart steeds opnieuw. ${core.loop_nl}`, code: "core_restart_loop" };
  }
  if (core.state === "restarting") {
    return { detail: "MediaGuard-core start opnieuw. Even geduld; deze pagina probeert het vanzelf opnieuw.", code: "core_starting" };
  }
  return fallback;
}

function proxy(req, res, dest) {
  const url = new URL(req.url ?? "/", "http://placeholder");
  const send = dest.base.protocol === "https:" ? httpsRequest : httpRequest;
  const upstream = send({
    protocol: dest.base.protocol,
    hostname: dest.base.hostname,
    port: dest.base.port || (dest.base.protocol === "https:" ? 443 : 80),
    method: req.method,
    path: dest.path + url.search,
    headers: forwardHeaders(req, dest.base),
  }, (upstreamRes) => {
    const headers = { ...upstreamRes.headers };
    if (String(headers["content-type"] ?? "").startsWith("text/event-stream")) {
      headers["cache-control"] = "no-cache, no-transform";
      headers["x-accel-buffering"] = "no";
    }
    res.writeHead(upstreamRes.statusCode ?? 502, headers);
    res.flushHeaders?.();
    upstreamRes.pipe(res);
  });
  upstream.setTimeout(0);
  upstream.on("error", () => {
    if (!res.headersSent) {
      res.writeHead(502, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
      res.end(JSON.stringify(coreDownDetail()));
    } else {
      res.destroy();
    }
  });
  req.on("aborted", () => upstream.destroy());
  res.on("close", () => upstream.destroy());
  req.pipe(upstream);
}

function proxyUpgrade(req, socket, head, dest) {
  const portNo = Number(dest.base.port || (dest.base.protocol === "https:" ? 443 : 80));
  const open = dest.base.protocol === "https:" ? tlsConnect : netConnect;
  const upstream = open({ host: dest.base.hostname, port: portNo, servername: dest.base.hostname }, () => {
    const url = new URL(req.url ?? "/", "http://placeholder");
    const headers = forwardHeaders(req, dest.base);
    headers.connection = "Upgrade";
    headers.upgrade = req.headers.upgrade ?? "websocket";
    const lines = [`GET ${dest.path}${url.search} HTTP/1.1`, ...Object.entries(headers).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`)];
    upstream.write(lines.join("\r\n") + "\r\n\r\n");
    if (head?.length) upstream.write(head);
    upstream.pipe(socket);
    socket.pipe(upstream);
  });
  const close = () => { socket.destroy(); upstream.destroy(); };
  upstream.on("error", close);
  socket.on("error", close);
}

// Standalone build (Docker): no next.config.ts / TypeScript at runtime — use the config that
// `next build` serialised, exactly like Next's own standalone server.js does.
const requiredFiles = new URL("./.next/required-server-files.json", import.meta.url);
if (!development && !process.env.__NEXT_PRIVATE_STANDALONE_CONFIG && existsSync(requiredFiles)) {
  process.env.__NEXT_PRIVATE_STANDALONE_CONFIG = JSON.stringify(JSON.parse(readFileSync(requiredFiles, "utf8")).config);
}

const application = next({ dev: development, hostname: host, port, dir: new URL(".", import.meta.url).pathname });
await application.prepare();
const handle = application.getRequestHandler();
const handleUpgrade = application.getUpgradeHandler();

// ---------------------------------------------------------------- compressie + cache
// Telefoon via een tunnel: JSON/JS/HTML gecomprimeerd (brotli of gzip) scheelt 60–90 % bytes.
// Nooit voor SSE (text/event-stream), WebSocket-upgrades of al gecomprimeerde antwoorden.
const COMPRESSIBLE = /json|javascript|text\/(html|css|plain)|image\/svg|xml|x-component/;

function prepare(req, res, { html }) {
  const ae = String(req.headers["accept-encoding"] ?? "");
  const enc = /\bbr\b/.test(ae) ? "br" : /\bgzip\b/.test(ae) ? "gzip" : null;
  const origSetHeader = res.setHeader.bind(res);
  // HTML nooit lang cachen (oude HTML na een update wijst naar verdwenen bestanden)
  res.setHeader = (name, value) => {
    if (html && String(name).toLowerCase() === "cache-control" && /s-maxage|max-age=[1-9]/.test(String(value))) {
      return origSetHeader(name, "no-cache");
    }
    return origSetHeader(name, value);
  };
  const origWrite = res.write.bind(res);
  const origEnd = res.end.bind(res);
  const origWriteHead = res.writeHead.bind(res);
  let stream = null;
  let decided = false;
  const decide = () => {
    if (decided) return;
    decided = true;
    if (!enc || req.method === "HEAD") return;
    const type = String(res.getHeader("content-type") ?? "");
    const length = Number(res.getHeader("content-length") ?? 0);
    if (res.getHeader("content-encoding") || res.statusCode === 204 || res.statusCode === 304) return;
    if (!COMPRESSIBLE.test(type) || type.includes("event-stream") || (length && length < 1024)) return;
    res.removeHeader("content-length");
    origSetHeader("content-encoding", enc);
    const vary = String(res.getHeader("vary") ?? "");
    origSetHeader("vary", vary ? `${vary}, Accept-Encoding` : "Accept-Encoding");
    stream = enc === "br"
      ? zlib.createBrotliCompress({ params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 4 } })
      : zlib.createGzip({ level: 6 });
    stream.on("data", (chunk) => origWrite(chunk));
    stream.on("end", () => origEnd());
    stream.on("error", () => origEnd());
    // Next en pipe() wachten op 'drain' van de response als write() false gaf
    stream.on("drain", () => res.emit("drain"));
  };
  res.writeHead = (status, ...args) => {
    const headers = args.find((a) => a && typeof a === "object");
    if (headers) {
      for (const [k, v] of Object.entries(headers)) if (v !== undefined) res.setHeader(k, v);
    }
    res.statusCode = status;
    decide();
    const message = args.find((a) => typeof a === "string");
    return message ? origWriteHead(status, message) : origWriteHead(status);
  };
  res.write = (chunk, encoding, cb) => {
    decide();
    if (!stream) return origWrite(chunk, encoding, cb);
    const ok = stream.write(chunk, encoding, cb);
    stream.flush(); // streamende HTML/RSC blijft stromen
    return ok;
  };
  res.end = (chunk, encoding, cb) => {
    decide();
    if (!stream) return origEnd(chunk, encoding, cb);
    if (chunk && typeof chunk !== "function") stream.end(chunk, encoding);
    else stream.end();
    return res;
  };
}

const server = createServer((req, res) => {
  const pathname = new URL(req.url ?? "/", "http://placeholder").pathname;
  const dest = target(pathname);
  if (dest) {
    if (!pathname.endsWith("/events/stream")) prepare(req, res, { html: false });
    proxy(req, res, dest);
    return;
  }
  prepare(req, res, { html: !pathname.startsWith("/_next/static/") });
  handle(req, res);
});

server.on("upgrade", (req, socket, head) => {
  const pathname = new URL(req.url ?? "/", "http://placeholder").pathname;
  const dest = target(pathname);
  if (dest) {
    proxyUpgrade(req, socket, head, dest);
    return;
  }
  handleUpgrade(req, socket, head);
});

server.listen(port, host, () => {
  console.log(`MediaGuard web luistert op ${host}:${port} · core ${coreUrl.origin}`);
});
