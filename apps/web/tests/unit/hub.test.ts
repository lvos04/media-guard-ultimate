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

  // Contract 1.3 §3.2 (C1.3-MGU-08): dezelfde lijsten als apps/core/tests/test_hub_url_setting.py (GOOD_13/BAD_13).
  it.each([
    ["http://hub.example.:30443", "http://hub.example:30443"],
    ["http://192.0.2.10.", "http://192.0.2.10"],
    ["https://xn--hb-via.example", "https://xn--hb-via.example"],
    ["http://[2001:DB8::5]", "http://[2001:db8::5]"],
    ["http://nas-1.example:08080", "http://nas-1.example:8080"],
    ["http://192.0.2.10:80", "http://192.0.2.10:80"],
  ])("contract 1.3: %s → %s", (raw, expected) => {
    expect(hubUrl(raw)).toBe(expected);
  });

  it.each([
    "http://127.1", "http://2130706433", "http://0x7f000001", "http://0x7f.1", "http://010.0.0.1",
    "http://0", "http://192.0.2.010", "http://256.1.2.3", "http://1.2.3.4.5", "http://foo.123",
    "http://localhost.", "http://localhost..", "http://127.0.0.1.", "http://0.1.2.3",
    "http://127%2e0%2e0%2e1", "http://hub%2eexample", "http://hub..example", "http://.hub.example",
    "http://-hub.example", "http://hub_x.example", "http://\u24dbocalhost",
    "http://\uff11\uff12\uff17.\uff10.\uff10.\uff11", "http://h\u00fcb.example", "http://[v1.x]",
    "http://[fe80::1%25eth0]:80", "http://[::ffff:7f00:1]", "http://[::ffff:127.0.0.1]", "http://[2001:db8::5]x",
    "http://hub.example:", "http://@hub.example", "http://hub.example:80@192.0.2.10", "http://192.0.2.10:65536",
  ])("contract 1.3: geen link voor %j (de browser zou er een ander adres van maken)", (raw) => {
    expect(hubUrl(raw)).toBeNull();
  });

  it("alleen tekst geeft een link", () => {
    expect(hubUrl(null)).toBeNull();
    expect(hubUrl(undefined)).toBeNull();
    expect(hubUrl(8080)).toBeNull();
  });
});
