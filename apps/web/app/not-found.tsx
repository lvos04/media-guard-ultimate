import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Pagina niet gevonden" };

/** Nederlandse 404 (vervangt "This page could not be found."). */
export default function NotFound() {
  return (
    <main className="auth-main" style={{ minHeight: "100vh" }} data-testid="not-found">
      <div className="state" style={{ maxWidth: 440 }}>
        <h1 className="state-title" style={{ fontSize: 24 }}>Deze pagina bestaat niet</h1>
        <div className="state-text">Het adres klopt niet (meer). Misschien is de pagina verplaatst; via het Overzicht vind je alles terug.</div>
        <Link href="/" className="btn btn--primary">Naar Overzicht</Link>
      </div>
    </main>
  );
}
