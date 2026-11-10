import { describe, expect, it } from "vitest";

import { hubUrl } from "@/lib/hub";

// Fictieve, gedocumenteerde testadressen (RFC 5737 / RFC 3849 / .example): geen NAS-gegevens.
describe("hubUrl (Terug naar de Hub, zelfde regels als apps/core/mediaguard/hub_url.py)", () => {
  it.each([
    ["http://192.0.2.10:8080", "http://192.0.2.10:8080"],
    ["https://hub.example:30443/", "https://hub.example:30443"],
    ["HTTP://Hub.Example", "http://hub.example"],
    ["http://[2001:db8::5]:9000", "http://[2001:db8::5]:9000"],
    ["  http://198.51.100.7  ", "http://198.51.100.7"],
  ])("%s → %s", (raw, expected) => {
    expect(hubUrl(raw)).toBe(expected);
  });

  it.each([
    "", "   ", "javascript:alert(1)", "ftp://192.0.2.10", "192.0.2.10:8080", "http://192.0.2.10:8080/hub",
    "http://192.0.2.10/?x=1", "http://192.0.2.10/#a", "http://192.0.2.10?", "http://user:pw@192.0.2.10",
    "http://localhost:8080", "http://app.localhost", "http://127.0.0.1", "http://127.1.2.3:80", "http://[::1]:80",
    "http://0.0.0.0:80", "http://[::]:80", "http://169.254.1.1", "http://[fe80::1]", "http://192.0.2.10 :80",
  ])("geen link voor %j", (raw) => {
    expect(hubUrl(raw)).toBeNull();
  });

  it("alleen tekst geeft een link", () => {
    expect(hubUrl(null)).toBeNull();
    expect(hubUrl(undefined)).toBeNull();
    expect(hubUrl(8080)).toBeNull();
  });
});
