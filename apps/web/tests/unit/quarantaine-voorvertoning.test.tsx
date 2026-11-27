// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";

import QuarantinePage from "@/app/(app)/quarantaine/page";
import { I18nProvider } from "@/lib/i18n";

const post = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/client", () => ({ api: { post } }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ can: () => true }) }));
vi.mock("@/lib/events", () => ({ useCoreEvents: () => {} }));
vi.mock("@/components/mg/toast", () => ({ useToast: () => ({ push: vi.fn() }) }));
vi.mock("@/components/mg/library-lock", () => ({ LibraryLockBanner: () => null }));
vi.mock("@/components/shell/space-tabs", () => ({ SpaceTabs: () => null }));
vi.mock("@/lib/api/hooks", () => ({
  useResource: (pad: string) => ({
    data: pad === "/quarantine" ? [{ id: 7, original_path: "proeffilm.mkv", status: "restorable",
      created_at: "2026-11-20", size: 80, reason: null }] : null,
    loading: false, error: null, reload: vi.fn(),
  }),
}));

it.each([
  { enig: true, tekst: "enige kopie" },
  { enig: false, tekst: "Alle geselecteerde bestanden blijven in quarantaine" },
])("laat een geblokkeerde selectie alleen sluiten (enige kopie $enig)", async ({ enig, tekst }) => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  post.mockReset().mockResolvedValue({
    count: 0, bytes: 0, unverified: [], blocked: enig ? [] : [7], kept_only_original: enig ? [7] : [],
  });
  const host = document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  try {
    await act(() => root.render(<I18nProvider locale="nl-NL"><QuarantinePage /></I18nProvider>));
    await act(() => host.querySelector<HTMLInputElement>('[data-testid="q-item-7"] input')!.click());
    await act(() => host.querySelector<HTMLButtonElement>('[data-testid="bulk-delete"]')!.click());
    const uitleg = document.querySelector<HTMLElement>('[role="dialog"]');
    expect(uitleg?.textContent).toContain(tekst);
    expect(uitleg?.querySelector("input")).toBeNull();
    const acties = uitleg!.querySelectorAll("button");
    expect([...acties].map((knop) => knop.textContent)).toEqual(["Sluiten"]);
    await act(() => acties[0].click());
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(post.mock.calls).toEqual([["/quarantine/bulk-delete", { item_ids: [7], confirm: false }]]);
  } finally {
    await act(() => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  }
});
