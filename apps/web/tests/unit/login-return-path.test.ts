// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import LoginPage from "@/app/login/page";
import { api } from "@/lib/api/client";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

vi.mock("@/lib/api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api/client")>(),
  api: { get: vi.fn(), post: vi.fn() },
}));

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.mocked(api.get).mockResolvedValue({ needs_setup: false });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function observeBrowserNavigation() {
  // jsdom maakt de publieke Location-methoden en de href-setter niet configureerbaar.
  const implementation = Object.getOwnPropertySymbols(window.location).find((key) => key.description === "impl");
  if (!implementation) throw new Error("De Location-implementatie van jsdom ontbreekt");
  const location = Reflect.get(window.location, implementation) as Pick<Location, "assign" | "replace" | "href">;
  return {
    "location.assign": vi.spyOn(location, "assign").mockImplementation(() => undefined),
    "location.replace": vi.spyOn(location, "replace").mockImplementation(() => undefined),
    "location.href": vi.spyOn(location, "href", "set").mockImplementation(() => undefined),
    "history.pushState": vi.spyOn(window.history, "pushState"),
    "history.replaceState": vi.spyOn(window.history, "replaceState"),
    "history.go": vi.spyOn(window.history, "go"),
    "history.back": vi.spyOn(window.history, "back"),
    "history.forward": vi.spyOn(window.history, "forward"),
  };
}

function fillInput(input: HTMLInputElement, value: string) {
  // De native setter laat React de invoer via het input-event verwerken.
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

it.each([
  { next: "//evil.example", destination: "/" },
  { next: "/%252Foverzicht", destination: "/" },
  { next: "/%", destination: "/" },
  { next: "/login?next=/", destination: "/" },
  { next: "/logout?bron=web#deel", destination: "/" },
  { next: "/anders/../api/v1/auth/%256cogout#deel", destination: "/" },
  { next: "/overzicht?tab=1#deel", destination: "/overzicht?tab=1#deel" },
].flatMap((test) => [
  { ...test, flow: "wachtwoord", twoFactor: false },
  { ...test, flow: "tweestapsverificatie", twoFactor: true },
]))("navigeert na $flow voor $next uitsluitend naar $destination", async ({ next, destination, twoFactor }) => {
  window.history.replaceState(null, "", `/login?${new URLSearchParams({ next })}`);
  const initialUrl = window.location.href;
  const browserNavigation = observeBrowserNavigation();
  vi.mocked(api.post)
    .mockResolvedValueOnce(twoFactor ? { needs_2fa: true, challenge: "testuitdaging" } : { needs_2fa: false })
    .mockResolvedValueOnce({ needs_2fa: false });

  await act(() => root.render(createElement(LoginPage)));
  await act(() => {
    fillInput(container.querySelector<HTMLInputElement>("#username")!, "testgebruiker");
    fillInput(container.querySelector<HTMLInputElement>("#password")!, "testwaarde");
  });
  await act(async () => container.querySelector<HTMLButtonElement>("button[type='submit']")!.click());
  expect(api.post).toHaveBeenCalledWith("/auth/login", { username: "testgebruiker", password: "testwaarde" });

  if (twoFactor) {
    expect(router.replace).not.toHaveBeenCalled();
    expect(router.push).not.toHaveBeenCalled();
    const form = container.querySelector<HTMLFormElement>("form[data-testid='login-2fa']")!;
    await act(() => fillInput(form.querySelector<HTMLInputElement>("#twofa-code")!, "123456"));
    await act(async () => form.querySelector<HTMLButtonElement>("button[type='submit']")!.click());
    expect(api.post).toHaveBeenCalledWith("/auth/login/2fa", { challenge: "testuitdaging", code: "123456" });
  }

  for (const [name, attempt] of Object.entries(browserNavigation)) {
    expect(attempt, name).not.toHaveBeenCalled();
  }
  expect(router.push, "router.push").not.toHaveBeenCalled();
  expect(router.replace.mock.calls, "router.replace").toEqual([[destination]]);
  expect(window.location.href).toBe(initialUrl);
});
