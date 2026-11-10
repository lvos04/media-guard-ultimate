#!/usr/bin/env node
// Zet de lucide-iconen die de Android-app gebruikt om naar Android-vector-drawables
// (app/src/main/res/drawable/lucide_<naam>.xml), zodat web en telefoon hetzelfde beeld hebben
// (docs/ontwerp/PEIL_MIGRATIE.md §10.2, ADR-0016 punt 11, ADR-0018). Geen bibliotheek in de app.
//
// Bron: het npm-pakket lucide-react (dezelfde versie als de web-app, apps/web/package.json).
//   node tools/lucide_naar_vector.mjs <pad naar node_modules/lucide-react>
// Zonder argument: ../web/node_modules/lucide-react (na `npm ci` in apps/web).
//
// Lucide tekent met lijnen (stroke 2, ronde uiteinden, geen vulling) in een vak van 24 × 24.
// Cirkels, rechthoeken, lijnen en polylijnen worden paden; padgegevens worden opnieuw
// geschreven met expliciete scheidingstekens (ook tussen boogvlaggen), zodat elke parser ze leest.
// De licentie (ISC, deels MIT via Feather) gaat mee naar app/src/main/assets/licenses/.
import { readFileSync, writeFileSync, mkdirSync, readdirSync, rmSync, copyFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HIER = dirname(fileURLToPath(import.meta.url));
const ANDROID = resolve(HIER, '..');
const BRON = resolve(process.argv[2] ?? join(ANDROID, '../web/node_modules/lucide-react'));
const DRAWABLE = join(ANDROID, 'app/src/main/res/drawable');
const LICENTIES = join(ANDROID, 'app/src/main/assets/licenses');

/** De iconen die de app gebruikt (lucide-naam). Nieuw icoon: hier toevoegen en het script opnieuw draaien. */
export const ICONEN = [
  // ondernavigatie (dezelfde als de web-navigatie, apps/web/components/shell/nav.ts) en merk
  'layout-dashboard', 'radar', 'library', 'hard-drive-download', 'inbox', 'shield-check',
  // status (Peil §4.3): Gezond, Beperkt, Storing, Onbekend, Verouderd, Gepauzeerd, Info, Niet gekoppeld,
  // Niet beschikbaar, Nog niet beschikbaar, gate, offline
  'circle-check', 'triangle-alert', 'octagon-x', 'circle-help', 'clock-alert', 'circle-pause', 'info', 'link-2-off',
  'circle-slash', 'hourglass', 'lock', 'wifi-off',
  // neutrale labels (beschikbaarheid, banen, bezig; PEIL_MIGRATIE §6.2)
  'check', 'file-x', 'search', 'calendar-clock', 'eye-off', 'trending-up', 'microchip', 'cpu', 'loader-circle',
  // kaarten, knoppen en meldingen
  'arrow-left', 'arrow-right', 'search-x', 'bell', 'bell-off', 'bell-ring', 'battery-warning', 'download',
  'cloud-download', 'zap', 'activity', 'hard-drive', 'list-checks', 'history', 'list-ordered', 'list', 'film', 'tv',
  'calendar-days', 'pause', 'play', 'octagon-alert', 'power-off', 'link-2', 'scan-qr-code', 'gauge', 'scale', 'rotate-ccw',
];

async function lees(naam, diepte = 0) {
  const bestand = join(BRON, 'dist/esm/icons', `${naam}.mjs`);
  const tekst = readFileSync(bestand, 'utf8');
  if (!/@license lucide-react v[\d.]+ - ISC/.test(tekst)) throw new Error(`${naam}: geen ISC-licentiekop`);
  // Een oude naam (bv. triangle-alert) verwijst door naar het icoon onder de nieuwe naam.
  const alias = tekst.match(/export \{ default \} from '\.\/([a-z0-9-]+)\.mjs'/);
  if (alias && diepte < 3) return lees(alias[1], diepte + 1);
  // __iconData = { name, size, node: [...] }
  const m = await import(pathToFileURL(bestand).href);
  if (!m.__iconData) throw new Error(`${naam}: geen __iconData in ${bestand} (andere lucide-react-versie?)`);
  return m.__iconData;
}

// ── SVG-pad opnieuw schrijven ─────────────────────────────────────────────────────────────────────
const PARAMS = { M: 2, L: 2, H: 1, V: 1, C: 6, S: 4, Q: 4, T: 2, A: 7, Z: 0 };

function tokens(d) {
  const uit = [];
  let i = 0;
  let cmd = null;
  let index = 0; // positie binnen de parameters van het huidige commando (voor boogvlaggen)
  const getal = /^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/;
  while (i < d.length) {
    const c = d[i];
    if (/[\s,]/.test(c)) { i++; continue; }
    if (/[MLHVCSQTAZmlhvcsqtaz]/.test(c)) { cmd = c; index = 0; uit.push(c); i++; continue; }
    if (cmd === null) throw new Error(`pad begint zonder commando: ${d}`);
    const n = PARAMS[cmd.toUpperCase()];
    if (cmd.toUpperCase() === 'A' && (index % 7 === 3 || index % 7 === 4)) {
      // Boogvlag: precies één teken 0 of 1, mag zonder scheiding tegen het volgende getal staan.
      if (c !== '0' && c !== '1') throw new Error(`ongeldige boogvlag '${c}' in ${d}`);
      uit.push(c); i++; index++; continue;
    }
    const m = d.slice(i).match(getal);
    if (!m) throw new Error(`onverwacht teken '${c}' in ${d}`);
    uit.push(m[0]); i += m[0].length; index++;
    if (n === 0) throw new Error(`getal na Z in ${d}`);
  }
  return uit;
}

const getalTekst = (v) => {
  const s = Number(v).toFixed(4).replace(/\.?0+$/, '');
  return s === '-0' ? '0' : s;
};

function normaliseer(d) {
  const t = tokens(d);
  const delen = [];
  for (const x of t) {
    if (/^[A-Za-z]$/.test(x)) delen.push(x);
    else delen.push(getalTekst(x));
  }
  // Commando's aan hun eerste getal; getallen met één spatie.
  return delen.join(' ').replace(/([A-Za-z]) /g, '$1');
}

const n = (v) => Number(v);
function cirkel(cx, cy, r) {
  return `M${cx - r},${cy}A${r},${r} 0 1 0 ${cx + r},${cy}A${r},${r} 0 1 0 ${cx - r},${cy}Z`;
}
function ellips(cx, cy, rx, ry) {
  return `M${cx - rx},${cy}A${rx},${ry} 0 1 0 ${cx + rx},${cy}A${rx},${ry} 0 1 0 ${cx - rx},${cy}Z`;
}
function rechthoek(x, y, w, h, rx, ry) {
  if (!rx && !ry) return `M${x},${y}H${x + w}V${y + h}H${x}Z`;
  rx = Math.min(rx || ry, w / 2);
  ry = Math.min(ry || rx, h / 2);
  return [
    `M${x + rx},${y}`, `H${x + w - rx}`, `A${rx},${ry} 0 0 1 ${x + w},${y + ry}`,
    `V${y + h - ry}`, `A${rx},${ry} 0 0 1 ${x + w - rx},${y + h}`,
    `H${x + rx}`, `A${rx},${ry} 0 0 1 ${x},${y + h - ry}`,
    `V${y + ry}`, `A${rx},${ry} 0 0 1 ${x + rx},${y}`, 'Z',
  ].join('');
}
function punten(p, sluit) {
  const v = p.trim().split(/[\s,]+/).map(Number);
  let s = `M${v[0]},${v[1]}`;
  for (let i = 2; i < v.length; i += 2) s += `L${v[i]},${v[i + 1]}`;
  return sluit ? `${s}Z` : s;
}

function naarPad([tag, a]) {
  switch (tag) {
    case 'path': return a.d;
    case 'circle': return cirkel(n(a.cx), n(a.cy), n(a.r));
    case 'ellipse': return ellips(n(a.cx), n(a.cy), n(a.rx), n(a.ry));
    case 'rect': return rechthoek(n(a.x ?? 0), n(a.y ?? 0), n(a.width), n(a.height), n(a.rx ?? 0), n(a.ry ?? 0));
    case 'line': return `M${a.x1},${a.y1}L${a.x2},${a.y2}`;
    case 'polyline': return punten(a.points, false);
    case 'polygon': return punten(a.points, true);
    default: throw new Error(`onbekend SVG-element ${tag}`);
  }
}

function vector(naam, icoon, versie) {
  const paden = icoon.node.map((el) => {
    if (el[1].fill && el[1].fill !== 'none') throw new Error(`${icoon.name}: gevuld element wordt niet ondersteund`);
    return `    <path
        android:fillColor="#00000000"
        android:pathData="${normaliseer(naarPad(el))}"
        android:strokeColor="#FF000000"
        android:strokeLineCap="round"
        android:strokeLineJoin="round"
        android:strokeWidth="2" />`;
  });
  return `<?xml version="1.0" encoding="utf-8"?>
<!-- lucide "${naam}"${icoon.name !== naam ? ` (= ${icoon.name})` : ''} uit lucide-react ${versie} (ISC; zie assets/licenses/lucide-LICENSE.txt).
     Gegenereerd door apps/android/tools/lucide_naar_vector.mjs; niet met de hand wijzigen. Kleur via de tint. -->
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp"
    android:height="24dp"
    android:viewportWidth="24"
    android:viewportHeight="24">
${paden.join('\n')}
</vector>
`;
}

const pakket = JSON.parse(readFileSync(join(BRON, 'package.json'), 'utf8'));
if (pakket.name !== 'lucide-react') throw new Error(`${BRON} is geen lucide-react`);
const verwacht = JSON.parse(readFileSync(join(ANDROID, '../web/package.json'), 'utf8')).dependencies?.['lucide-react'];
if (verwacht && verwacht.replace(/^[\^~]/, '') !== pakket.version) {
  throw new Error(`lucide-react ${pakket.version} wijkt af van apps/web/package.json (${verwacht})`);
}

mkdirSync(DRAWABLE, { recursive: true });
for (const f of readdirSync(DRAWABLE)) if (f.startsWith('lucide_')) rmSync(join(DRAWABLE, f));
for (const naam of ICONEN) {
  const icoon = await lees(naam);
  writeFileSync(join(DRAWABLE, `lucide_${naam.replaceAll('-', '_')}.xml`), vector(naam, icoon, pakket.version));
}
mkdirSync(LICENTIES, { recursive: true });
copyFileSync(join(BRON, 'LICENSE'), join(LICENTIES, 'lucide-LICENSE.txt'));
console.log(`${ICONEN.length} iconen uit lucide-react ${pakket.version} → ${DRAWABLE}`);
