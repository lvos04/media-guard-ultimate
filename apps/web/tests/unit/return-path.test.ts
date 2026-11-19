import { describe, expect, it } from "vitest";
import { returnPath } from "@/lib/return-path";

describe("terugkeerpad na inloggen", () => {
  it.each([
    null,
    "",
    "//evil.example",
    "/\\evil",
    "https://evil.example",
    "/a://b",
    "/een pad",
    "/é",
    `/${"a".repeat(256)}`,
    "/login",
    "/login?next=/",
    "/login#deel",
    "/login/",
    "/%6cogin",
    "/anders/../login",
  ])("weigert %j", (value) => {
    expect(returnPath(value)).toBe("/");
  });

  it.each(["/", "/overzicht?tab=1#deel", `/${"a".repeat(255)}`, "/login-hulp"])("behoudt %j", (value) => {
    expect(returnPath(value)).toBe(value);
  });
});
