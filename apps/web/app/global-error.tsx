"use client";

// Laatste vangnet als zelfs de basisopmaak faalt: eenvoudige Nederlandse pagina zonder afhankelijkheden.
// Peil (review stap 5): de opmaak kan hier niet uit globals.css komen (deze pagina vervangt de hele layout), dus staan de
// benodigde Peil 1.1.0-tokenwaarden (licht/donker volgens het systeem) hieronder letterlijk: tekst ≥ 7:1, knoprand ≥ 3:1,
// tweekleurige focusring, 44 px, geen opacity op tekst. Waarden gelijk aan design/ontwerptaal/tokens.json.
const CSS = `
:root { color-scheme: light dark; --bg: #EEF1F5; --fg: #0B0F14; --fg2: #353E4A; --primary: #123DA3; --on-primary: #FFFFFF; --edge: #5B6575; --ring: #123DA3; --halo: #FFFFFF; }
@media (prefers-color-scheme: dark) { :root { --bg: #0A0D12; --fg: #F2F5F8; --fg2: #C2CAD5; --primary: #8DB6FF; --on-primary: #06101F; --edge: #8E99A8; --ring: #8DB6FF; --halo: #0A0D12; } }
body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg); color: var(--fg);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif; font-size: 16px; line-height: 1.5; }
.mg-ge { text-align: center; padding: 24px; max-width: 26rem; }
.mg-ge h1 { font-size: 24px; line-height: 32px; margin: 0 0 8px; }
.mg-ge p { margin: 0 0 16px; color: var(--fg2); }
.mg-ge .acties { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
.mg-ge .knop { min-height: 44px; padding: 0 16px; border-radius: 8px; border: 2px solid var(--edge); background: transparent; color: var(--fg);
  display: inline-flex; align-items: center; font: inherit; font-weight: 600; text-decoration: none; cursor: pointer; }
.mg-ge .knop--primair { background: var(--primary); border-color: var(--primary); color: var(--on-primary); }
.mg-ge :focus-visible { outline: 3px solid var(--ring); outline-offset: 2px; box-shadow: 0 0 0 2px var(--halo); }
`;

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="nl">
      <head><style>{CSS}</style></head>
      <body>
        <main className="mg-ge">
          <h1>Er ging iets mis</h1>
          <p>MediaGuard kon deze pagina niet tonen. Je media en instellingen zijn niet aangeraakt.</p>
          <div className="acties">
            <button type="button" className="knop knop--primair" onClick={reset}>Opnieuw proberen</button>
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- volledige herlaadbeurt is hier juist de bedoeling */}
            <a href="/" className="knop">Naar Overzicht</a>
          </div>
        </main>
      </body>
    </html>
  );
}
