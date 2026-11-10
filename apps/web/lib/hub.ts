// "Terug naar de Hub" (Peil O-11, ADR-0016 punt 1): the address comes from the optional setting `system.hub_url`
// (GET /api/v1/settings/effective, readable for every role). The UI shows the link ONLY for a value that passes the
// same rules as the core validator (apps/core/mediaguard/hub_url.py; contract 1.3 §3.2 and §2.3 rule 5, ADR-0021):
// http(s)://<host>[:<port>] without path, query, fragment or credentials; never loopback, link-local, localhost or
// 0.0.0.0/::; IPv4 only as canonical a.b.c.d (a host ending in a number or 0x-part that is not canonical — 127.1,
// 2130706433, 0x7f000001, 010.0.0.1 — is refused, because the browser would turn it into another address); IPv6 only
// in [] without zone; one trailing dot on a name is ignored; ASCII names only (no %-escapes or IDN, which the browser
// would rewrite). The rules run on the RAW text, before `new URL` normalises it. Anything else → no link in the DOM.
// There is never a default or example address.
export const HUB_URL_SETTING = "system.hub_url";

const LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const NUMERIC_LABEL = /^(?:0x[0-9a-f]*|[0-9]+)$/;
const OCTET = "(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])";
const IPV4 = new RegExp(`^${OCTET}(?:\\.${OCTET}){3}$`);

function blockedV4(ip: string): boolean {
  const [a, b] = ip.split(".").map(Number);
  return a === 127 || a === 0 || (a === 169 && b === 254);
}

/** Canonical bracketed IPv6 host (lower case) or null when it is invalid or not allowed. */
function ipv6Host(inner: string): string | null {
  if (!inner || inner.includes("%") || !/^[0-9a-fA-F:.]+$/.test(inner)) return null;
  let normalised: string;
  try { normalised = new URL(`http://[${inner}]`).hostname.replace(/^\[|\]$/g, ""); } catch { return null; }
  if (normalised === "::" || normalised === "::1") return null;
  if (/^fe[89ab][0-9a-f]?:/.test(normalised)) return null; // fe80::/10
  const mapped = /^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/.exec(normalised);
  if (mapped) {
    const hi = parseInt(mapped[1], 16);
    const v4 = `${hi >> 8}.${hi & 255}.${parseInt(mapped[2], 16) >> 8}.${parseInt(mapped[2], 16) & 255}`;
    if (blockedV4(v4)) return null;
  }
  return `[${inner.toLowerCase()}]`;
}

/** Canonical host for the link, or null. `authority` has no credentials (those are refused before). */
function hostOf(authority: string): { host: string; port: string | undefined } | null {
  const bracketed = /^\[([^\]]*)\](?::([0-9]+))?$/.exec(authority);
  if (bracketed) {
    const host = ipv6Host(bracketed[1]);
    return host ? { host, port: bracketed[2] } : null;
  }
  const plain = /^([^:[\]]*)(?::([0-9]+))?$/.exec(authority);
  if (!plain) return null;
  let host = plain[1].toLowerCase();
  if (host.endsWith(".")) host = host.slice(0, -1); // one trailing dot does not count
  const labels = host.split(".");
  if (!host || labels.some((l) => !l)) return null;
  if (NUMERIC_LABEL.test(labels[labels.length - 1])) {
    return IPV4.test(host) && !blockedV4(host) ? { host, port: plain[2] } : null;
  }
  if (!labels.every((l) => LABEL.test(l))) return null;
  if (host === "localhost" || host.endsWith(".localhost")) return null;
  return { host, port: plain[2] };
}

/** Returns the normalised Hub URL (scheme://host[:port]) or null when the value must not produce a link. */
export function hubUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const raw = value.trim();
  // printable ASCII only: no whitespace, no IDN/fullwidth characters, no backslash
  if (!raw || !/^[\x21-\x7e]+$/.test(raw) || raw.includes("\\")) return null;
  const m = /^(https?):\/\/([^/?#]*)\/?$/i.exec(raw);
  if (!m) return null;
  const authority = m[2];
  if (authority.includes("@")) return null;
  const parsed = hostOf(authority);
  if (!parsed) return null;
  let port = "";
  if (parsed.port !== undefined) {
    const n = Number(parsed.port);
    if (!Number.isInteger(n) || n > 65535) return null;
    port = `:${n}`;
  }
  return `${m[1].toLowerCase()}://${parsed.host}${port}`;
}
