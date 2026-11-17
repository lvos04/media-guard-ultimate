"use client";

import { OctagonAlert } from "lucide-react";

import { StatusChip } from "@/components/mg/primitives";
import { useI18n, type MessageKey } from "@/lib/i18n";

/**
 * Rechten (scopes) van API-sleutels (S-P3-600, docs/ontwerp/API_SLEUTEL_SCOPES_ONTWERP.md). De core kent nu alleen
 * `emergency_stop:on` (de noodstop aanzetten, nooit opheffen); rechten kunnen alleen op een kijker-sleutel.
 */
export const KNOWN_SCOPES = ["emergency_stop:on"] as const;
export type Scope = (typeof KNOWN_SCOPES)[number];
export type KeyRole = "viewer" | "operator";

/** Per bekend recht: korte id (voor `apikey-scope-<id>` in het formulier), naam en uitleg. */
export const SCOPE_TEXT: Record<Scope, { id: string; label: MessageKey; hint: MessageKey }> = {
  "emergency_stop:on": { id: "estop", label: "apikeys.scope.estopOn", hint: "apikeys.scope.estopOnHint" },
};

export function isKnownScope(scope: string): scope is Scope {
  return (KNOWN_SCOPES as readonly string[]).includes(scope);
}

/** Wat het formulier meestuurt: alleen bij rol kijker en alleen als er iets aan staat; anders niets (= geen rechten). */
export function scopesForRequest(role: KeyRole, scopes: readonly Scope[]): Scope[] | undefined {
  return role === "viewer" && scopes.length ? [...scopes] : undefined;
}

/**
 * Labels per recht (tabelcel Rol en de intrekdialoog). Bekend = neutraal label met het noodstopicoon; onbekend = de
 * vorm Onbekend met de code en één regel uitleg (Peil §4.3: nooit groen, altijd met reden). Geen of lege rechten:
 * niets, zodat een sleutel zonder rechten eruitziet zoals altijd.
 */
export function ScopeChips({ scopes, testId }: { scopes?: readonly string[] | null; testId?: string }) {
  const { t } = useI18n();
  if (!scopes?.length) return null;
  const unknown = scopes.some((s) => !isKnownScope(s));
  return (
    <>
      <div className="flex flex-wrap gap-1" data-testid={testId}>
        {scopes.map((s) => isKnownScope(s)
          ? <StatusChip key={s} tone="neutral" outline icon={OctagonAlert}>{t(SCOPE_TEXT[s].label)}</StatusChip>
          : <StatusChip key={s} tone="unknown">{t("apikeys.scope.unknown", { scope: s })}</StatusChip>)}
      </div>
      {unknown ? <div className="cell-sub">{t("apikeys.scope.unknownHint")}</div> : null}
    </>
  );
}
