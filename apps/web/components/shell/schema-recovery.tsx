import { TriangleAlert } from "lucide-react";

export async function schemaRecoveryMessage(): Promise<string | null> {
  try {
    const core = process.env.MG_CORE_URL ?? "http://localhost:8100";
    const response = await fetch(new URL("/api/v1/health", core), {
      cache: "no-store",
      signal: AbortSignal.timeout(1500),
    });
    if (response.status !== 503) return null;
    const health = await response.json();
    return health.reason_code === "schema_nieuwer" && typeof health.message === "string"
      ? health.message
      : null;
  } catch {
    return null;
  }
}

export function SchemaRecovery({ message }: { message: string }) {
  return (
    <main id="main" className="auth-main min-h-svh px-4 py-8">
      <section aria-labelledby="herstel-titel" className="auth-card w-full min-w-0">
        <div className="stack">
          <span className="list-icon"><TriangleAlert className="ico" aria-hidden="true" /></span>
          <h1 id="herstel-titel" className="page-title">MediaGuard staat in herstelweergave</h1>
          <p>{message}</p>
          <p>Aanmelden en het dashboard zijn tijdelijk niet beschikbaar.</p>
          <ul className="list-disc space-y-2 pl-5">
            <li>Kies in het formulier van de TrueNAS Custom App de image-versie die bij de gegevens hoort.</li>
            <li>Of zet in TrueNAS de snapshot van vóór de update terug.</li>
          </ul>
          <p className="text-muted-foreground">Open MediaGuard opnieuw zodra de app weer draait.</p>
        </div>
      </section>
    </main>
  );
}
