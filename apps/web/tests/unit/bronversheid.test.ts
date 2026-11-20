import { describe, expect, it } from "vitest";

import { combinedReading, responseTime, sourceReading } from "@/lib/reading";

const now = Date.UTC(2026, 0, 2, 8);
const oud = now - 90_000;
const res = { data: {}, fetchedAt: now, stale: false };

describe("versheid van de eigen bron", () => {
  it("een oude, ongeldige of ontbrekende taakmeting wordt niet vernieuwd door HTTP 200", () => {
    expect(sourceReading(res, now, new Date(oud).toISOString())).toEqual({ state: "stale", at: oud });
    for (const tijd of [null, "", "ongeldig", new Date(now + 60_000).toISOString()])
      expect(sourceReading(res, now, tijd)).toEqual({ state: "unknown", at: null });
  });
  it("behoudt de ouderdom van een cacheantwoord en telt Date en Age slechts één keer", () => {
    const kop = new Headers({ Date: new Date(oud).toUTCString(), Age: "90" });
    expect(responseTime(kop, now)).toEqual({ at: oud, stale: false });
    kop.set("X-MediaGuard-Stale", "95");
    expect(responseTime(kop, now)).toEqual({ at: now - 95_000, stale: true });
    expect(sourceReading({ ...res, observedAt: oud }, now, new Date(now).toISOString())).toEqual({ state: "stale", at: oud });
  });
  it("een toekomstige datering wordt ook bij een oude transportmeting niet vertrouwd", () => {
    const toekomst = new Date(now + 60_000);
    expect(responseTime(new Headers({ Date: toekomst.toUTCString() }), now).at).toBeNull();
    expect(sourceReading({ ...res, observedAt: oud }, now, toekomst.toISOString()).state).toBe("unknown");
  });
  it("een lege lijst heeft een meettijd, een geweigerde bron en een ongedateerde combinatie niet", () => {
    const vers = sourceReading({ ...res, data: [] }, now);
    const uitval = sourceReading({ ...res, data: [], error: true }, now);
    const onbekend = sourceReading({ ...res, data: null, error: true }, now);
    expect(vers.state).toBe("fresh");
    expect(uitval).toEqual({ state: "stale", at: now });
    expect(combinedReading([vers, uitval])).toEqual(uitval);
    expect(combinedReading([vers, onbekend])).toEqual({ state: "unknown", at: null });
  });
});
