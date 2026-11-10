import Link from "next/link";

export default function AppNotFound() {
  return (
    <div className="state" data-testid="not-found">
      <h1 className="state-title" style={{ fontSize: 24 }}>Deze pagina bestaat niet</h1>
      <div className="state-text">Het adres klopt niet (meer). Via het Overzicht vind je alles terug.</div>
      <Link href="/" className="btn btn--primary">Naar Overzicht</Link>
    </div>
  );
}
