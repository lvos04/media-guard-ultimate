// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";

import { DownloadCleanup } from "@/components/mg/download-cleanup";

const toast = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("@/components/mg/toast", () => ({ useToast: () => toast }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ can: () => true }) }));
vi.mock("@/lib/api/client", () => ({ api: { post: async () => ({ restored: true, partial: true }) } }));
vi.mock("@/lib/api/hooks", () => ({
  useResource: () => ({
    data: {
      enabled: false, trash_days: 7, freed_total_bytes: 0, trash_bytes: 4,
      trash: [{ id: 1, name: "proefmap", reason: "test", size: 4, purge_after: "2026-11-19",
        manual_only: true, partial: true }],
    },
    reload: vi.fn(),
  }),
}));

it("meldt een onderbroken verwijdering in de prullenbak en bij het terugzetten", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const container = document.createElement("div");
  const root = createRoot(container);
  try {
    await act(() => root.render(createElement(DownloadCleanup)));
    expect(container.textContent).toContain("De map kan onvolledig zijn");
    expect(container.textContent).toContain("Automatisch verwijderen gestopt");
    await act(async () => container.querySelector("button")!.click());
    expect(toast.push).toHaveBeenCalledWith("info", "Alleen de resterende inhoud is teruggezet.");
  } finally {
    await act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});
