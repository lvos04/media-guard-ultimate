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
    "/%2Foverzicht",
    "/%252Foverzicht",
    "/%5Coverzicht",
    "/%255Coverzicht",
    "/a%3A%2F%2Fb",
    "/a%253A%252F%252Fb",
    "/%",
    "/%FF",
    "/%/../overzicht",
    "/%2525252561",
    "/anders/..//overzicht",
    "/een pad",
    "/é",
    `/${"a".repeat(256)}`,
    "/login",
    "/login?next=/",
    "/login#deel",
    "/login/",
    "/%6cogin",
    "/anders/../login",
    "/anders/%252e%252e/%256cogin?tab=1#deel",
    "/logout",
    "/LOGOUT/?tab=1#deel",
    "/anders/%252e%252e/%256cogout#deel",
    "/api/v1/auth/logout?bron=web",
    "/api//v1/auth/%256cogout#deel",
  ])("weigert %j", (value) => {
    expect(returnPath(value)).toBe("/");
  });

  it.each([
    "/",
    "/overzicht?tab=1#deel",
    "/overzicht?tab=%2F#deel%25",
    "/%25252561",
    `/${"a".repeat(255)}`,
    "/login-hulp",
  ])("behoudt %j", (value) => {
    expect(returnPath(value)).toBe(value);
  });
});
