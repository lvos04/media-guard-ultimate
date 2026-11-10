import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "MediaGuard", template: "%s · MediaGuard" },
  description: "Media Operations Control Center",
  icons: { icon: "/favicon.svg" },
};

// Peil: themeColor volgt het opgeloste kleurschema (background-token uit ontwerptaal.css, licht/donker).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#EEF1F5" },
    { media: "(prefers-color-scheme: dark)", color: "#0A0D12" },
  ],
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    // Peil O-01: systeemfont en systeem-mono (ontwerptaal.css --font-sans/--font-mono); geen webfonts, geen
    // fontverzoeken (Inter en JetBrains Mono zijn weg sinds fase 2d, PEIL_MIGRATIE §6.5).
    <html lang="nl" suppressHydrationWarning>
      <head>
        {/* Apply the per-user theme before first paint (no flash). Default "system" (Peil O-02, ADR-0016 punt 5): the
            script ALWAYS sets the resolved data-theme="dark|light" — without the attribute Peil is light. The key
            mg.theme and the per-user choice are unchanged; the setting ui.theme is applied after login. */}
        <script dangerouslySetInnerHTML={{ __html: "(function(){var r=document.documentElement,c='system';try{c=localStorage.getItem('mg.theme')||'system'}catch(e){}if(c!=='dark'&&c!=='light')c='system';var d=c==='dark'||(c==='system'&&window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches);r.dataset.theme=d?'dark':'light';r.dataset.themeChoice=c})()" }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
